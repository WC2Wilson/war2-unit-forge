from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Callable

from . import process_backend as backend

RECORD_SIZE = 24
DESCRIPTOR_SIZE = 8
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40

# Remaster status/identity table immediately follows the 120 gCards descriptors.
# Each 16-byte gPorts entry is {icon, frame, string, pad, query_cb, draw_cb}.
PORT_RECORD_SIZE = 16
PORT_TABLE_EXPECTED_RVA = 0x004C6308

# PC equivalent of the classic file-278 unit grouping step.  The engine normally
# copies unitType into unitGroup, so an unused type such as 34 otherwise indexes
# the wrong animation group even when its graphics pointer is valid.
UNIT_GROUP_UPDATE_RVA = 0x000C95D0
UNIT_GROUP_UPDATE_ORIGINAL = b"\x55\x8B\xEC\x8B\x4D\x08"

# unit_set_sequence indexes the sequence-script table by unitType, not unitGroup.
# Unit 34 must remain type 34 for gameplay/card identity, but its sequence lookup
# must use the selected visual donor or movement/death can execute unrelated art.
UNIT_SEQUENCE_LOOKUP_RVA = 0x000DFEE0
UNIT_SEQUENCE_TABLE_RVA = 0x005340B8
UNIT_SEQUENCE_LOOKUP_ORIGINAL = bytes.fromhex(
    "55 8B EC 8B 15 B8 40 93 00 53 8A 5D 0C 56 8B 75 08 0F B6 46 27"
)

# The status panel resolves the 16-bit string ID in gPorts through this table.
# The table is a count word followed by count 16-bit offsets and NUL strings.
UNIT_STRING_TABLE_POINTER_RVA = 0x0053434C
CUSTOM_UNIT_STRING_ID = 36
MAX_CUSTOM_UNIT_NAME_BYTES = 63

# The status-card renderer only rebuilds its cached button controls when this
# byte is set.  The tiny source function at RVA 0xE8150 writes this exact flag.
CARD_DIRTY_RVA = 0x00534325
CARD_DIRTY_SETTER_RVA = 0x000E8150

PRODUCER_MISMATCH_BRANCH_RVA = 0x000AC873
PRODUCER_MISMATCH_ORIGINAL = b"\x75\x29"
PRODUCER_MISMATCH_PATCH = b"\x90\x90"

# Native bldg_build_man performs two independent per-unit lookups.  The old
# compatibility NOP bypassed only the producer-byte comparison; unused slots
# still had a NULL eligibility callback and therefore displayed a button that
# did nothing.  Clone both entries from a known trainable donor instead.
TRAINING_PRODUCER_TABLE_RVA = 0x00438248
TRAINING_ELIGIBILITY_TABLE_RVA = 0x004C0428
TRAINABLE_UNIT_LIMIT = 58

# Non-DAT identity tables used by the remaster sound frontend.  The four-byte
# table selects the unit voice callback (selection/acknowledgement), while the
# two-byte table supplies the completed-unit creation sound.  Unused/alternate
# unit slots retain their original Peasant/Peon or null entries unless these are
# explicitly cloned from the gameplay donor.
UNIT_VOICE_CALLBACK_TABLE_RVA = 0x004C3828
UNIT_CREATE_SOUND_TABLE_RVA = 0x004C37B0
UNIT_VOICE_CALLBACK_SIZE = 4
UNIT_CREATE_SOUND_SIZE = 2

# bldg_build_start calls a per-unit eligibility callback after validating the
# producer byte.  Donor callbacks can intentionally become false (for example,
# the Archer callback becomes false after the Ranger upgrade).  A custom unit
# needs its own stable callback rather than inheriting that mutable donor rule.
ALWAYS_TRAINABLE_CALLBACK = b"\xB8\x01\x00\x00\x00\xC3"  # mov eax,1 / ret

if hasattr(backend, "kernel32"):
    kernel32 = backend.kernel32
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.FlushInstructionCache.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    kernel32.FlushInstructionCache.restype = wintypes.BOOL
else:
    kernel32 = None


def get_field(raw: bytes, name: str) -> int:
    info = backend.FIELD_INFO[name]
    return struct.unpack_from("<" + info["fmt"], raw, info["offset"])[0]


def set_field(raw: bytes, name: str, value: int) -> bytes:
    info = backend.FIELD_INFO[name]
    fmt = info["fmt"]
    maximum = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}[fmt]
    if not 0 <= int(value) <= maximum:
        raise ValueError(f"{info['label']} must be between 0 and 0x{maximum:X}.")
    result = bytearray(raw)
    struct.pack_into("<" + fmt, result, info["offset"], int(value))
    return bytes(result)


@dataclass
class EditableButton:
    raw: bytes
    label: str


class RuntimeCards:
    def __init__(self, process: backend.ProcessMemory, database: backend.CardDatabase):
        self.process = process
        self.database = database
        self.card_names = list(database.reference.get("cards", []))
        self.card_count = len(self.card_names)
        self.gcards_rva = 0
        self.attach_descriptors: dict[int, bytes] = {}
        self.factory_descriptors: dict[int, bytes] = {}
        self.allocations: dict[int, int] = {}
        self.port_snapshots: dict[int, bytes] = {}
        self.group_hook_original: bytes = b""
        self.group_hook_allocation = 0
        self.group_hook_source: int | None = None
        self.group_hook_target: int | None = None
        self.group_hook_dynamic_class_bits: int | None = None
        self.group_hook_recovered = False
        self.group_alias_map: dict[int, tuple[int, int]] = {}
        self.sequence_hook_original: bytes = b""
        self.sequence_hook_allocation = 0
        self.sequence_hook_source: int | None = None
        self.sequence_hook_target: int | None = None
        self.sequence_hook_recovered = False
        self.sequence_alias_map: dict[int, int] = {}
        self.name_table_original_pointer = 0
        self.name_table_allocation = 0
        self.name_table_size = 0
        self.custom_name: str | None = None
        self.training_metadata_snapshots: dict[int, tuple[int, int]] = {}
        self.training_callback_allocations: dict[int, int] = {}
        self.sound_alias_snapshots: dict[int, tuple[bytes, bytes]] = {}
        self.sequence_table_snapshots: dict[int, tuple[int, int]] = {}

    def locate_descriptor_table(
        self,
        progress: Callable[[str], None] | None = None,
        cancel_event=None,
    ) -> int:
        if progress:
            progress("Reading Warcraft II module and locating gCards...")
        module = self.process.read(self.process.base, self.process.size)
        if not module:
            raise RuntimeError("Could not read the Warcraft II module.")

        bases = self.database.mapping.get("confirmed_card_bases", {})
        arrays = self.database.reference.get("arrays", {})
        known: list[tuple[int, int, int]] = []
        for card_id, array_name in enumerate(self.card_names):
            if not array_name or array_name not in bases:
                continue
            known.append(
                (
                    card_id,
                    self.process.base + int(bases[array_name]),
                    len(arrays.get(array_name, [])),
                )
            )
        if not known:
            raise RuntimeError("No confirmed command-card bases are available.")

        anchor_run: list[tuple[int, int, int]] = []
        expected_next = None
        for item in known:
            card_id = item[0]
            if expected_next is None or card_id == expected_next:
                anchor_run.append(item)
                expected_next = card_id + 1
                if len(anchor_run) >= 12:
                    break
            elif len(anchor_run) < 6:
                anchor_run = [item]
                expected_next = card_id + 1
            else:
                break
        if len(anchor_run) < 4:
            anchor_run = known[: min(8, len(known))]

        first_id, first_pointer, _ = anchor_run[0]
        needle = struct.pack("<I", first_pointer)
        search_at = 0
        checked = 0
        best: tuple[int, int, int] | None = None
        while checked < 4096:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Attach cancelled.")
            found = module.find(needle, search_at)
            if found < 0:
                break
            search_at = found + 1
            table_offset = found - 4 - first_id * DESCRIPTOR_SIZE
            if table_offset < 0 or table_offset % 4:
                continue
            if table_offset + self.card_count * DESCRIPTOR_SIZE > len(module):
                continue
            checked += 1

            anchor_matches = 0
            for card_id, expected_pointer, _count in anchor_run:
                entry = table_offset + card_id * DESCRIPTOR_SIZE
                pointer = struct.unpack_from("<I", module, entry + 4)[0]
                if pointer == expected_pointer:
                    anchor_matches += 1
                else:
                    break
            if anchor_matches < len(anchor_run):
                continue

            pointer_matches = 0
            count_matches = 0
            for card_id, expected_pointer, expected_count in known:
                entry = table_offset + card_id * DESCRIPTOR_SIZE
                count = struct.unpack_from("<H", module, entry)[0]
                pointer = struct.unpack_from("<I", module, entry + 4)[0]
                if pointer == expected_pointer:
                    pointer_matches += 1
                if count == expected_count:
                    count_matches += 1
            score = pointer_matches * 10 + count_matches
            candidate = (score, pointer_matches, table_offset)
            if best is None or candidate > best:
                best = candidate
            if pointer_matches >= max(20, len(known) - 3):
                break

        if best is None:
            raise RuntimeError(f"Could not locate gCards; checked {checked} candidate(s).")
        _, pointer_matches, table_offset = best
        minimum_matches = min(12, max(5, len(known) // 5))
        if pointer_matches < minimum_matches:
            raise RuntimeError(
                f"Possible gCards table did not validate strongly enough ({pointer_matches} pointers)."
            )
        self.gcards_rva = table_offset
        if progress:
            progress(
                f"Found gCards at module+0x{table_offset:08X} ({pointer_matches} confirmed pointers)."
            )
        return table_offset

    def descriptor_address(self, card_id: int) -> int:
        if not self.gcards_rva:
            raise RuntimeError("gCards has not been located.")
        return self.process.base + self.gcards_rva + card_id * DESCRIPTOR_SIZE

    def read_descriptor(self, card_id: int) -> bytes:
        raw = self.process.read(self.descriptor_address(card_id), DESCRIPTOR_SIZE)
        if len(raw) != DESCRIPTOR_SIZE:
            raise RuntimeError(f"Could not read descriptor for card 0x{card_id:02X}.")
        return raw

    @staticmethod
    def unpack_descriptor(raw: bytes) -> tuple[int, int]:
        return struct.unpack_from("<H", raw, 0)[0], struct.unpack_from("<I", raw, 4)[0]

    @staticmethod
    def pack_descriptor(count: int, pointer: int, padding: bytes = b"\x00\x00") -> bytes:
        return struct.pack("<H", int(count)) + padding[:2] + struct.pack("<I", int(pointer))

    def capture_descriptors(self, progress: Callable[[str], None] | None = None) -> None:
        bases = self.database.mapping.get("confirmed_card_bases", {})
        arrays = self.database.reference.get("arrays", {})
        table_size = self.card_count * DESCRIPTOR_SIZE
        table = self.process.read(self.process.base + self.gcards_rva, table_size)
        if len(table) != table_size:
            raise RuntimeError("Could not read all command-card descriptors.")
        self.attach_descriptors.clear()
        self.factory_descriptors.clear()
        for card_id, array_name in enumerate(self.card_names):
            offset = card_id * DESCRIPTOR_SIZE
            attach = table[offset : offset + DESCRIPTOR_SIZE]
            self.attach_descriptors[card_id] = attach
            if array_name and array_name in bases:
                pointer = self.process.base + int(bases[array_name])
                count = len(arrays.get(array_name, []))
                factory = self.pack_descriptor(count, pointer, attach[2:4])
            else:
                factory = attach
            self.factory_descriptors[card_id] = factory
        if progress:
            progress(f"Captured {self.card_count} live card descriptors.")

    def load_records(self, descriptor: bytes) -> list[bytes]:
        count, pointer = self.unpack_descriptor(descriptor)
        if count == 0:
            return []
        if pointer == 0 or count > 4096:
            raise RuntimeError(f"Invalid command-card descriptor: count={count}, pointer=0x{pointer:08X}.")
        raw = self.process.read(pointer, count * RECORD_SIZE)
        if len(raw) != count * RECORD_SIZE:
            raise RuntimeError(f"Could not read {count} button records at 0x{pointer:08X}.")
        return [raw[i : i + RECORD_SIZE] for i in range(0, len(raw), RECORD_SIZE)]

    def load_current_records(self, card_id: int) -> list[bytes]:
        return self.load_records(self.read_descriptor(card_id))

    def module_record(self, array_name: str, row_index: int) -> bytes:
        rows = self.database.effective.get(array_name, {})
        if row_index not in rows:
            raise RuntimeError(f"No confirmed record for {array_name} row {row_index}.")
        raw = self.process.read(self.process.base + int(rows[row_index]), RECORD_SIZE)
        if len(raw) != RECORD_SIZE:
            raise RuntimeError(f"Could not read {array_name} row {row_index}.")
        return raw

    def _flush_instruction_cache(self, address: int, size: int) -> None:
        if kernel32 and self.process.handle and address and size:
            kernel32.FlushInstructionCache(
                self.process.handle,
                ctypes.c_void_p(address),
                ctypes.c_size_t(size),
            )

    def _validate_card_dirty_flag(self) -> None:
        setter = self.process.read(self.process.base + CARD_DIRTY_SETTER_RVA, 8)
        expected_address = self.process.base + CARD_DIRTY_RVA
        if (
            len(setter) != 8
            or setter[:2] != b"\xC6\x05"
            or struct.unpack_from("<I", setter, 2)[0] != expected_address
            or setter[6:] != b"\x01\xC3"
        ):
            raise RuntimeError(
                "The remaster command-card dirty flag did not validate for this build."
            )

    def mark_card_dirty(self) -> None:
        self._validate_card_dirty_flag()
        address = self.process.base + CARD_DIRTY_RVA
        if not self.process.write_protected(address, b"\x01"):
            raise RuntimeError("Could not request a remaster command-card redraw.")
        if self.process.read(address, 1) != b"\x01":
            raise RuntimeError("The command-card redraw flag failed verification.")

    def ports_rva(self) -> int:
        if not self.gcards_rva:
            raise RuntimeError("gCards has not been located.")
        rva = self.gcards_rva + self.card_count * DESCRIPTOR_SIZE
        if rva != PORT_TABLE_EXPECTED_RVA:
            raise RuntimeError(
                f"Derived gPorts RVA 0x{rva:08X} did not match the supported remaster layout "
                f"0x{PORT_TABLE_EXPECTED_RVA:08X}."
            )
        return rva

    def _port_address(self, unit_id: int) -> int:
        if not 0 <= int(unit_id) < self.card_count:
            raise ValueError(f"Unit ID {unit_id} is outside 0-{self.card_count - 1}.")
        return self.process.base + self.ports_rva() + int(unit_id) * PORT_RECORD_SIZE

    def read_portrait_identity(self, unit_id: int) -> tuple[int, int, int, int, int, int]:
        raw = self.process.read(self._port_address(unit_id), PORT_RECORD_SIZE)
        if len(raw) != PORT_RECORD_SIZE:
            raise RuntimeError(f"Could not read Unit {unit_id} gPorts identity.")
        return struct.unpack("<HHHHII", raw)

    def install_portrait_identity(
        self,
        source_id: int,
        target_id: int,
        *,
        string_id: int | None = None,
        callback_source_id: int | None = None,
    ) -> tuple[int, int, int]:
        source_address = self._port_address(source_id)
        callback_source_address = self._port_address(
            source_id if callback_source_id is None else callback_source_id
        )
        target_address = self._port_address(target_id)
        source = self.process.read(source_address, PORT_RECORD_SIZE)
        callback_source = self.process.read(callback_source_address, PORT_RECORD_SIZE)
        target = self.process.read(target_address, PORT_RECORD_SIZE)
        if (
            len(source) != PORT_RECORD_SIZE
            or len(callback_source) != PORT_RECORD_SIZE
            or len(target) != PORT_RECORD_SIZE
        ):
            raise RuntimeError("Could not read the remaster gPorts identity records.")

        icon_id, frame_id, donor_string_id, _icon_pad, _icon_query, _icon_draw = struct.unpack(
            "<HHHHII", source
        )
        _status_icon, _status_frame, _status_string, pad, query_cb, draw_cb = struct.unpack(
            "<HHHHII", callback_source
        )
        module_start = self.process.base
        module_end = module_start + self.process.size
        if not (module_start <= query_cb < module_end and module_start <= draw_cb < module_end):
            raise RuntimeError(
                f"Source Unit {source_id} gPorts callbacks were outside Warcraft II.exe."
            )
        if icon_id == 0xFFFF or donor_string_id == 0xFFFF:
            raise RuntimeError(f"Source Unit {source_id} has an invalid identity record.")
        final_string_id = donor_string_id if string_id is None else int(string_id)
        if not 1 <= final_string_id <= 0xFFFF:
            raise ValueError("Status-panel string IDs must be in the range 1-65535.")
        identity = struct.pack(
            "<HHHHII", icon_id, frame_id, final_string_id, pad, query_cb, draw_cb
        )

        self.port_snapshots.setdefault(int(target_id), target)
        if target != identity:
            if not self.process.write_protected(target_address, identity):
                raise RuntimeError("Could not install the target unit icon/name identity record.")
            if self.process.read(target_address, PORT_RECORD_SIZE) != identity:
                raise RuntimeError("The target unit identity record failed verification.")
        self.mark_card_dirty()
        return icon_id, final_string_id, target_address

    def install_portrait_alias(self, source_id: int, target_id: int) -> tuple[int, int, int]:
        return self.install_portrait_identity(source_id, target_id)

    @staticmethod
    def build_custom_string_table_blob(
        original_blob: bytes,
        count: int,
        string_id: int,
        encoded_name: bytes,
    ) -> bytes:
        if not 1 <= int(string_id) <= int(count):
            raise ValueError(f"Custom string ID {string_id} is outside the table count {count}.")
        if not encoded_name or b"\x00" in encoded_name:
            raise ValueError("The custom unit name must contain non-NUL text.")
        header_size = 2 + int(count) * 2
        if len(original_blob) < header_size:
            raise ValueError("The source string table is shorter than its offset header.")
        result = bytearray(original_blob)
        new_offset = len(result)
        if new_offset > 0xFFFF or new_offset + len(encoded_name) + 1 > 0x10000:
            raise ValueError("The cloned string table would exceed its 16-bit offset format.")
        struct.pack_into("<H", result, 2 + (int(string_id) - 1) * 2, new_offset)
        result.extend(encoded_name)
        result.append(0)
        return bytes(result)

    def _read_live_string_table(self, pointer: int) -> tuple[int, bytes]:
        count_raw = self.process.read(pointer, 2)
        if len(count_raw) != 2:
            raise RuntimeError("Could not read the unit-name string-table header.")
        count = struct.unpack("<H", count_raw)[0]
        if not 64 <= count <= 4096:
            raise RuntimeError(f"The unit-name string table has an implausible count: {count}.")
        offsets_raw = self.process.read(pointer + 2, count * 2)
        if len(offsets_raw) != count * 2:
            raise RuntimeError("Could not read the unit-name string offsets.")
        offsets = struct.unpack("<" + "H" * count, offsets_raw)
        maximum_end = 2 + count * 2
        for offset in sorted(set(offsets)):
            if not offset:
                continue
            if offset < 2 + count * 2 or offset >= 0x10000:
                raise RuntimeError(f"Invalid string-table offset 0x{offset:04X}.")
            raw = self.process.read(pointer + offset, min(512, 0x10000 - offset))
            if not raw:
                raise RuntimeError(f"Could not read string data at offset 0x{offset:04X}.")
            end = raw.find(b"\x00")
            if end < 0:
                raise RuntimeError(f"String at offset 0x{offset:04X} was not NUL-terminated.")
            maximum_end = max(maximum_end, offset + end + 1)
        blob = self.process.read(pointer, maximum_end)
        if len(blob) != maximum_end:
            raise RuntimeError("Could not copy the complete unit-name string table.")
        return count, blob

    def install_custom_name(
        self,
        target_id: int,
        name: str,
        *,
        string_id: int = CUSTOM_UNIT_STRING_ID,
    ) -> tuple[int, int, int]:
        clean = " ".join(str(name).replace("\r", " ").replace("\n", " ").split()).strip()
        if not clean:
            raise ValueError("Enter a custom unit name before installing it.")
        try:
            encoded = clean.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("Custom unit names currently support Windows-1252 characters only.") from exc
        if len(encoded) > MAX_CUSTOM_UNIT_NAME_BYTES:
            raise ValueError(
                f"Custom unit names are limited to {MAX_CUSTOM_UNIT_NAME_BYTES} encoded bytes."
            )

        pointer_address = self.process.base + UNIT_STRING_TABLE_POINTER_RVA
        current_pointer = self.process.read_u32(pointer_address) or 0
        if self.name_table_allocation:
            # Rebuild from the original game table, not from our previous clone.
            source_pointer = self.name_table_original_pointer
        else:
            source_pointer = current_pointer
            self.name_table_original_pointer = current_pointer
        if not source_pointer:
            raise RuntimeError("The live unit-name string-table pointer is null.")
        count, original_blob = self._read_live_string_table(source_pointer)
        new_blob = self.build_custom_string_table_blob(original_blob, count, string_id, encoded)
        allocation = self.allocate(len(new_blob))
        if not self.process.write_protected(allocation, new_blob):
            self.free(allocation)
            raise RuntimeError("Could not write the cloned unit-name string table.")
        if self.process.read(allocation, len(new_blob)) != new_blob:
            self.free(allocation)
            raise RuntimeError("The cloned unit-name string table failed verification.")
        if not self.process.write_protected(pointer_address, struct.pack("<I", allocation)):
            self.free(allocation)
            raise RuntimeError("Could not activate the custom unit-name string table.")
        if self.process.read_u32(pointer_address) != allocation:
            self.process.write_protected(pointer_address, struct.pack("<I", source_pointer))
            self.free(allocation)
            raise RuntimeError("The custom unit-name pointer failed verification.")
        old_allocation = self.name_table_allocation
        self.name_table_allocation = allocation
        self.name_table_size = len(new_blob)
        self.custom_name = clean
        if old_allocation and old_allocation != allocation:
            self.free(old_allocation)

        target_address = self._port_address(target_id)
        target = self.process.read(target_address, PORT_RECORD_SIZE)
        if len(target) != PORT_RECORD_SIZE:
            raise RuntimeError("Could not read the target gPorts record for custom naming.")
        identity = bytearray(target)
        struct.pack_into("<H", identity, 4, int(string_id))
        self.port_snapshots.setdefault(int(target_id), target)
        if not self.process.write_protected(target_address, bytes(identity)):
            raise RuntimeError("Could not assign the custom name string ID to the target unit.")
        if self.process.read(target_address, PORT_RECORD_SIZE) != bytes(identity):
            raise RuntimeError("The target unit custom-name identity failed verification.")
        self.mark_card_dirty()
        return int(string_id), allocation, len(new_blob)

    def restore_custom_name(self) -> bool:
        if not self.name_table_allocation:
            return False
        pointer_address = self.process.base + UNIT_STRING_TABLE_POINTER_RVA
        original = self.name_table_original_pointer
        if not original:
            raise RuntimeError("The original unit-name table pointer was not captured.")
        if not self.process.write_protected(pointer_address, struct.pack("<I", original)):
            raise RuntimeError("Could not restore the original unit-name string table.")
        if self.process.read_u32(pointer_address) != original:
            raise RuntimeError("The original unit-name table pointer failed verification.")
        allocation = self.name_table_allocation
        self.name_table_original_pointer = 0
        self.name_table_allocation = 0
        self.name_table_size = 0
        self.custom_name = None
        self.free(allocation)
        self.mark_card_dirty()
        return True

    def restore_portrait_aliases(self) -> int:
        restored = 0
        for target_id, original in list(self.port_snapshots.items()):
            address = self._port_address(target_id)
            if not self.process.write_protected(address, original):
                raise RuntimeError(f"Could not restore Unit {target_id} gPorts identity.")
            if self.process.read(address, PORT_RECORD_SIZE) != original:
                raise RuntimeError(f"Unit {target_id} gPorts restore failed verification.")
            restored += 1
        self.port_snapshots.clear()
        if restored:
            self.mark_card_dirty()
        return restored

    def allocate_executable(self, size: int) -> int:
        if not kernel32:
            raise RuntimeError("Live executable allocation is available only on Windows.")
        address = kernel32.VirtualAllocEx(
            self.process.handle,
            None,
            int(size),
            MEM_COMMIT | MEM_RESERVE,
            PAGE_EXECUTE_READWRITE,
        )
        value = int(address or 0)
        if not value:
            raise RuntimeError("VirtualAllocEx failed for the unit-group hook.")
        if value > 0xFFFFFFFF:
            kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(value), 0, MEM_RELEASE)
            raise RuntimeError("The unit-group hook allocation was outside the x86 address space.")
        return value

    @staticmethod
    def _rel32(source_after_instruction: int, destination: int) -> bytes:
        displacement = int(destination) - int(source_after_instruction)
        if not -(1 << 31) <= displacement < (1 << 31):
            raise RuntimeError("The x86 hook target was outside rel32 range.")
        return struct.pack("<i", displacement)

    @staticmethod
    def _decode_entry_jump(hook_address: int, entry: bytes, total_size: int) -> int | None:
        """Resolve one of Unit Forge's E9 entry hooks without trusting its target."""
        if len(entry) != int(total_size) or len(entry) < 5 or entry[0] != 0xE9:
            return None
        if any(value != 0x90 for value in entry[5:]):
            return None
        displacement = struct.unpack_from("<i", entry, 1)[0]
        target = int(hook_address) + 5 + displacement
        if not 0 < target <= 0xFFFFFFFF:
            return None
        return target

    @staticmethod
    def _cave_jump_returns(
        cave: bytes,
        cave_address: int,
        jump_offset: int,
        cave_size: int,
        expected_return: int,
    ) -> bool:
        if jump_offset < 0 or cave_size < jump_offset + 5 or len(cave) < cave_size:
            return False
        if cave[jump_offset] != 0xE9:
            return False
        displacement = struct.unpack_from("<i", cave, jump_offset + 1)[0]
        actual = int(cave_address) + int(cave_size) + displacement
        return actual == int(expected_return)

    @classmethod
    def inspect_group_hook_cave(
        cls, cave: bytes, cave_address: int, hook_address: int
    ) -> tuple[int, int, int, int, str] | None:
        """Recognize Unit Forge 0.2.4+ group caves, including pre-classification builds."""
        common = bytes.fromhex("55 8B EC 8B 4D 08 8A 41 27 3C")
        if len(cave) < 34 or not cave.startswith(common):
            return None
        target_id = cave[10]
        expected_return = int(hook_address) + len(UNIT_GROUP_UPDATE_ORIGINAL)

        # 0.2.4/0.2.5 payload: group alias only, no cached air/sea correction.
        if (
            cave[11:16] == bytes.fromhex("75 10 80 79 2B")
            and cave[17:26] == bytes.fromhex("74 08 80 49 06 20 C6 41 2B")
            and cave[27:30] == bytes.fromhex("5D C3 E9")
            and cave[16] == cave[26]
            and cls._cave_jump_returns(cave, cave_address, 29, 34, expected_return)
        ):
            return cave[16], target_id, 0, 34, "legacy"

        # 0.2.6+ payload: first clear the cached air/sea target-class bits.
        if cave[13:17] != bytes.fromhex("80 61 1C F3"):
            return None
        dynamic_bits = 0
        body_offset = 17
        expected_jne = 0x14
        cave_size = 38
        jump_offset = 33
        if cave[17:20] == bytes.fromhex("80 49 1C"):
            dynamic_bits = cave[20]
            if dynamic_bits not in {0x04, 0x08, 0x0C}:
                return None
            body_offset = 21
            expected_jne = 0x18
            cave_size = 42
            jump_offset = 37
        body = bytes.fromhex("80 79 2B")
        suffix = bytes.fromhex("74 08 80 49 06 20 C6 41 2B")
        if (
            cave[11] != 0x75
            or cave[12] != expected_jne
            or cave[body_offset : body_offset + 3] != body
            or cave[body_offset + 4 : body_offset + 13] != suffix
            or cave[body_offset + 14 : body_offset + 17] != bytes.fromhex("5D C3 E9")
        ):
            return None
        source_id = cave[body_offset + 3]
        if cave[body_offset + 13] != source_id:
            return None
        if not cls._cave_jump_returns(
            cave, cave_address, jump_offset, cave_size, expected_return
        ):
            return None
        return source_id, target_id, dynamic_bits, cave_size, "current"

    @classmethod
    def inspect_sequence_hook_cave(
        cls, cave: bytes, cave_address: int, hook_address: int, module_base: int
    ) -> tuple[int, int, bytes] | None:
        replay_size = len(UNIT_SEQUENCE_LOOKUP_ORIGINAL)
        cave_size = replay_size + 11
        if len(cave) < cave_size:
            return None
        replay = bytes(cave[:replay_size])
        if not cls.sequence_prologue_matches(replay, module_base):
            return None
        if cave[replay_size : replay_size + 6] != bytes(
            [0x3C, cave[replay_size + 1], 0x75, 0x02, 0xB0, cave[replay_size + 5]]
        ):
            return None
        jump_offset = replay_size + 6
        if not cls._cave_jump_returns(
            cave,
            cave_address,
            jump_offset,
            cave_size,
            int(hook_address) + replay_size,
        ):
            return None
        return cave[replay_size + 5], cave[replay_size + 1], replay

    @classmethod
    def build_group_hook_cave(
        cls,
        source_id: int,
        target_id: int,
        cave_address: int,
        hook_address: int,
        dynamic_class_bits: int = 0,
    ) -> bytes:
        """Legacy single-alias builder retained for validation and old-hook recovery tests."""
        source_id = int(source_id)
        target_id = int(target_id)
        dynamic_class_bits = int(dynamic_class_bits)
        code = bytearray([
            0x55, 0x8B, 0xEC, 0x8B, 0x4D, 0x08, 0x8A, 0x41, 0x27,
            0x3C, target_id, 0x75, 0x00, 0x80, 0x61, 0x1C, 0xF3,
        ])
        if dynamic_class_bits:
            code.extend([0x80, 0x49, 0x1C, dynamic_class_bits])
        code.extend([
            0x80, 0x79, 0x2B, source_id, 0x74, 0x08,
            0x80, 0x49, 0x06, 0x20, 0xC6, 0x41, 0x2B, source_id,
            0x5D, 0xC3,
        ])
        replay_offset = len(code)
        code[12] = replay_offset - 13
        code.append(0xE9)
        size = len(code) + 4
        code.extend(cls._rel32(int(cave_address) + size, int(hook_address) + len(UNIT_GROUP_UPDATE_ORIGINAL)))
        return bytes(code)

    @classmethod
    def build_group_hook_cave_multi(
        cls,
        aliases: dict[int, tuple[int, int]],
        cave_address: int,
        hook_address: int,
    ) -> bytes:
        """Build one unit-group hook that supports every registered custom unit.

        aliases maps target unit ID -> (graphics/group donor ID, cached air/sea bits).
        The hook replays the original prologue, then handles any matching target,
        and jumps back to the original function for ordinary units.
        """
        normalized: list[tuple[int, int, int]] = []
        for target_id, pair in sorted(aliases.items()):
            source_id, dynamic_class_bits = map(int, pair)
            target_id = int(target_id)
            if not 0 <= source_id <= 0xFF or not 0 <= target_id <= 0xFF:
                raise ValueError("Unit group aliases require byte-sized unit IDs.")
            if dynamic_class_bits not in {0x00, 0x04, 0x08, 0x0C}:
                raise ValueError("Dynamic unit classification must use only air/sea bits 0x04 and 0x08.")
            normalized.append((target_id, source_id, dynamic_class_bits))
        if not normalized:
            raise ValueError("At least one unit-group alias is required.")

        # Original function entry plus a skipped marker that lets a later Forge
        # window recognize and safely replace this multi-unit hook.
        code = bytearray(UNIT_GROUP_UPDATE_ORIGINAL)
        code.extend(b"\xEB\x04UFMG")
        code.extend(b"\x8A\x41\x27")  # mov al,[ecx+27] (unitType)
        for target_id, source_id, dynamic_class_bits in normalized:
            block = bytearray([
                0x3C, target_id,       # cmp al,target
                0x75, 0x00,            # jne next block
                0x80, 0x61, 0x1C, 0xF3,  # clear cached air/sea bits
            ])
            if dynamic_class_bits:
                block.extend([0x80, 0x49, 0x1C, dynamic_class_bits])
            block.extend([
                0x80, 0x79, 0x2B, source_id,  # cmp byte [ecx+2B],source
                0x74, 0x08,                    # je return
                0x80, 0x49, 0x06, 0x20,        # or byte [ecx+06],20
                0xC6, 0x41, 0x2B, source_id,  # mov byte [ecx+2B],source
                0x5D, 0xC3,                    # pop ebp / ret
            ])
            skip = len(block) - 4
            if skip > 0x7F:
                raise RuntimeError("A unit-group alias block exceeded short-branch range.")
            block[3] = skip
            code.extend(block)
        code.append(0xE9)
        final_size = len(code) + 4
        code.extend(cls._rel32(
            int(cave_address) + final_size,
            int(hook_address) + len(UNIT_GROUP_UPDATE_ORIGINAL),
        ))
        return bytes(code)

    @classmethod
    def inspect_group_hook_cave_multi(
        cls, cave: bytes, cave_address: int, hook_address: int
    ) -> bool:
        prefix = UNIT_GROUP_UPDATE_ORIGINAL + b"\xEB\x04UFMG\x8A\x41\x27"
        if not cave.startswith(prefix):
            return False
        # A valid Forge multi hook always ends its active code with a rel32 jump
        # back to original+6. Search a conservative first page for that jump.
        expected = int(hook_address) + len(UNIT_GROUP_UPDATE_ORIGINAL)
        for offset in range(len(prefix), min(len(cave) - 4, 0x400)):
            if cave[offset] != 0xE9:
                continue
            disp = struct.unpack_from("<i", cave, offset + 1)[0]
            if int(cave_address) + offset + 5 + disp == expected:
                return True
        return False

    def _install_group_alias_map(self) -> tuple[int, int]:
        if not self.group_alias_map:
            raise ValueError("No unit-group aliases are registered.")
        hook_address = self.process.base + UNIT_GROUP_UPDATE_RVA
        original = self.process.read(hook_address, len(UNIT_GROUP_UPDATE_ORIGINAL))
        cave = 0
        recovered = False
        if original == UNIT_GROUP_UPDATE_ORIGINAL:
            cave = self.allocate_executable(0x1000)
        else:
            old_cave = self._decode_entry_jump(hook_address, original, len(UNIT_GROUP_UPDATE_ORIGINAL)) or 0
            probe = self.process.read(old_cave, 0x400) if old_cave else b""
            previous_single = self.inspect_group_hook_cave(probe, old_cave, hook_address) if old_cave else None
            previous_multi = self.inspect_group_hook_cave_multi(probe, old_cave, hook_address) if old_cave else False
            if not previous_single and not previous_multi:
                raise RuntimeError(
                    f"Unit-group function at RVA 0x{UNIT_GROUP_UPDATE_RVA:X} has unexpected bytes "
                    f"{original.hex(' ').upper()}; expected the original prologue or a verified Unit Forge hook. "
                    "Restart Warcraft if another tool owns this function."
                )
            if not self.process.write_protected(hook_address, UNIT_GROUP_UPDATE_ORIGINAL):
                raise RuntimeError("Could not suspend the existing Unit Forge group hook.")
            self._flush_instruction_cache(hook_address, len(UNIT_GROUP_UPDATE_ORIGINAL))
            cave = old_cave
            recovered = True

        cave_data = self.build_group_hook_cave_multi(self.group_alias_map, cave, hook_address)
        if not self.process.write_protected(cave, cave_data):
            if not recovered:
                self.free(cave)
            raise RuntimeError("Could not write the multi-unit group hook.")
        if self.process.read(cave, len(cave_data)) != cave_data:
            if not recovered:
                self.free(cave)
            raise RuntimeError("The multi-unit group hook failed verification.")
        self._flush_instruction_cache(cave, len(cave_data))
        patch = b"\xE9" + self._rel32(hook_address + 5, cave) + b"\x90"
        if not self.process.write_protected(hook_address, patch):
            if not recovered:
                self.free(cave)
            raise RuntimeError("Could not activate the multi-unit group hook.")
        if self.process.read(hook_address, len(patch)) != patch:
            self.process.write_protected(hook_address, UNIT_GROUP_UPDATE_ORIGINAL)
            if not recovered:
                self.free(cave)
            raise RuntimeError("The multi-unit group hook activation failed verification.")
        self._flush_instruction_cache(hook_address, len(patch))
        self.group_hook_original = UNIT_GROUP_UPDATE_ORIGINAL
        self.group_hook_allocation = cave
        self.group_hook_recovered = recovered
        # Compatibility fields retain the most recently registered alias.
        last_target = sorted(self.group_alias_map)[-1]
        self.group_hook_target = last_target
        self.group_hook_source, self.group_hook_dynamic_class_bits = self.group_alias_map[last_target]
        return hook_address, cave

    def install_group_alias(
        self, source_id: int, target_id: int, dynamic_class_bits: int = 0
    ) -> tuple[int, int]:
        source_id = int(source_id)
        target_id = int(target_id)
        dynamic_class_bits = int(dynamic_class_bits)
        current = self.group_alias_map.get(target_id)
        requested = (source_id, dynamic_class_bits)
        if self.group_hook_allocation and current == requested:
            return self.process.base + UNIT_GROUP_UPDATE_RVA, self.group_hook_allocation
        self.group_alias_map[target_id] = requested
        return self._install_group_alias_map()

    def restore_group_alias(self) -> bool:
        if not self.group_hook_allocation:
            self.group_alias_map.clear()
            return False
        hook_address = self.process.base + UNIT_GROUP_UPDATE_RVA
        original = self.group_hook_original or UNIT_GROUP_UPDATE_ORIGINAL
        if not self.process.write_protected(hook_address, original):
            raise RuntimeError("Could not restore the original unit-group function.")
        if self.process.read(hook_address, len(original)) != original:
            raise RuntimeError("The original unit-group function failed restore verification.")
        self._flush_instruction_cache(hook_address, len(original))
        cave = self.group_hook_allocation
        self.group_hook_original = b""
        self.group_hook_allocation = 0
        self.group_hook_source = None
        self.group_hook_target = None
        self.group_hook_dynamic_class_bits = None
        self.group_hook_recovered = False
        self.group_alias_map.clear()
        self.free(cave)
        return True

    @staticmethod
    def sequence_prologue_matches(original: bytes, module_base: int) -> bool:
        if len(original) != len(UNIT_SEQUENCE_LOOKUP_ORIGINAL):
            return False
        if original[:5] != UNIT_SEQUENCE_LOOKUP_ORIGINAL[:5]:
            return False
        if original[9:] != UNIT_SEQUENCE_LOOKUP_ORIGINAL[9:]:
            return False
        relocated_pointer = struct.unpack_from("<I", original, 5)[0]
        return relocated_pointer == int(module_base) + UNIT_SEQUENCE_TABLE_RVA

    @classmethod
    def build_sequence_hook_cave(
        cls,
        source_id: int,
        target_id: int,
        cave_address: int,
        hook_address: int,
        original: bytes | None = None,
    ) -> bytes:
        """Legacy single-alias builder retained for old-hook validation."""
        replay = bytes(original or UNIT_SEQUENCE_LOOKUP_ORIGINAL)
        prefix = replay + bytes([0x3C, int(target_id), 0x75, 0x02, 0xB0, int(source_id), 0xE9])
        size = len(prefix) + 4
        return prefix + cls._rel32(int(cave_address) + size, int(hook_address) + len(replay))

    @classmethod
    def build_sequence_hook_cave_multi(
        cls,
        aliases: dict[int, int],
        cave_address: int,
        hook_address: int,
        original: bytes,
    ) -> bytes:
        replay = bytes(original)
        if len(replay) != len(UNIT_SEQUENCE_LOOKUP_ORIGINAL):
            raise ValueError("The unit-sequence prologue has the wrong length.")
        normalized = [(int(t), int(s)) for t, s in sorted(aliases.items())]
        if not normalized:
            raise ValueError("At least one sequence alias is required.")
        code = bytearray(replay)
        code.extend(b"\xEB\x04UFMS")
        end_jumps: list[int] = []
        for target_id, source_id in normalized:
            if not 0 <= target_id <= 0xFF or not 0 <= source_id <= 0xFF:
                raise ValueError("Sequence aliases require byte-sized unit IDs.")
            code.extend([0x3C, target_id, 0x75, 0x04, 0xB0, source_id, 0xEB, 0x00])
            end_jumps.append(len(code) - 1)
        final_jump_offset = len(code)
        for displacement_offset in end_jumps:
            displacement = final_jump_offset - (displacement_offset + 1)
            if not 0 <= displacement <= 0x7F:
                raise RuntimeError("Multi-unit sequence hook exceeded short-branch range.")
            code[displacement_offset] = displacement
        code.append(0xE9)
        total_size = len(code) + 4
        code.extend(cls._rel32(
            int(cave_address) + total_size,
            int(hook_address) + len(replay),
        ))
        return bytes(code)

    @classmethod
    def inspect_sequence_hook_cave_multi(
        cls, cave: bytes, cave_address: int, hook_address: int, module_base: int
    ) -> bytes | None:
        replay_size = len(UNIT_SEQUENCE_LOOKUP_ORIGINAL)
        if len(cave) < replay_size + 11:
            return None
        replay = bytes(cave[:replay_size])
        if not cls.sequence_prologue_matches(replay, module_base):
            return None
        if cave[replay_size:replay_size + 6] != b"\xEB\x04UFMS":
            return None
        expected = int(hook_address) + replay_size
        for offset in range(replay_size + 6, min(len(cave) - 4, 0x400)):
            if cave[offset] != 0xE9:
                continue
            disp = struct.unpack_from("<i", cave, offset + 1)[0]
            if int(cave_address) + offset + 5 + disp == expected:
                return replay
        return None

    def _install_sequence_alias_map(self) -> tuple[int, int]:
        if not self.sequence_alias_map:
            raise ValueError("No sequence aliases are registered.")
        hook_address = self.process.base + UNIT_SEQUENCE_LOOKUP_RVA
        original = self.process.read(hook_address, len(UNIT_SEQUENCE_LOOKUP_ORIGINAL))
        cave = 0
        recovered = False
        replay = original
        if self.sequence_prologue_matches(original, self.process.base):
            cave = self.allocate_executable(0x1000)
        else:
            old_cave = self._decode_entry_jump(hook_address, original, len(UNIT_SEQUENCE_LOOKUP_ORIGINAL)) or 0
            probe = self.process.read(old_cave, 0x400) if old_cave else b""
            previous_single = self.inspect_sequence_hook_cave(
                probe, old_cave, hook_address, self.process.base
            ) if old_cave else None
            previous_multi = self.inspect_sequence_hook_cave_multi(
                probe, old_cave, hook_address, self.process.base
            ) if old_cave else None
            if previous_multi is not None:
                replay = previous_multi
            elif previous_single:
                replay = previous_single[2]
            else:
                expected_pointer = self.process.base + UNIT_SEQUENCE_TABLE_RVA
                raise RuntimeError(
                    f"Unit sequence function at RVA 0x{UNIT_SEQUENCE_LOOKUP_RVA:X} has unexpected bytes "
                    f"{original.hex(' ').upper()}. Expected the original opcode shape with its "
                    f"ASLR-relocated sequence pointer 0x{expected_pointer:08X}, or a verified Unit Forge hook."
                )
            if not self.process.write_protected(hook_address, replay):
                raise RuntimeError("Could not suspend the existing Unit Forge sequence hook.")
            self._flush_instruction_cache(hook_address, len(replay))
            cave = old_cave
            recovered = True
        cave_data = self.build_sequence_hook_cave_multi(
            self.sequence_alias_map, cave, hook_address, replay
        )
        if not self.process.write_protected(cave, cave_data):
            if not recovered:
                self.free(cave)
            raise RuntimeError("Could not write the multi-unit sequence hook.")
        if self.process.read(cave, len(cave_data)) != cave_data:
            if not recovered:
                self.free(cave)
            raise RuntimeError("The multi-unit sequence hook failed verification.")
        self._flush_instruction_cache(cave, len(cave_data))
        patch = b"\xE9" + self._rel32(hook_address + 5, cave)
        patch += b"\x90" * (len(replay) - len(patch))
        if not self.process.write_protected(hook_address, patch):
            if not recovered:
                self.free(cave)
            raise RuntimeError("Could not activate the multi-unit sequence hook.")
        if self.process.read(hook_address, len(patch)) != patch:
            self.process.write_protected(hook_address, replay)
            if not recovered:
                self.free(cave)
            raise RuntimeError("The multi-unit sequence hook activation failed verification.")
        self._flush_instruction_cache(hook_address, len(patch))
        self.sequence_hook_original = replay
        self.sequence_hook_allocation = cave
        self.sequence_hook_recovered = recovered
        last_target = sorted(self.sequence_alias_map)[-1]
        self.sequence_hook_target = last_target
        self.sequence_hook_source = self.sequence_alias_map[last_target]
        return hook_address, cave

    def install_sequence_alias(self, source_id: int, target_id: int) -> tuple[int, int]:
        source_id = int(source_id)
        target_id = int(target_id)
        if self.sequence_hook_allocation and self.sequence_alias_map.get(target_id) == source_id:
            return self.process.base + UNIT_SEQUENCE_LOOKUP_RVA, self.sequence_hook_allocation
        self.sequence_alias_map[target_id] = source_id
        return self._install_sequence_alias_map()

    def restore_sequence_alias(self) -> bool:
        if not self.sequence_hook_allocation:
            self.sequence_alias_map.clear()
            return False
        hook_address = self.process.base + UNIT_SEQUENCE_LOOKUP_RVA
        original = self.sequence_hook_original or UNIT_SEQUENCE_LOOKUP_ORIGINAL
        if not self.process.write_protected(hook_address, original):
            raise RuntimeError("Could not restore the original unit-sequence function.")
        if self.process.read(hook_address, len(original)) != original:
            raise RuntimeError("The original unit-sequence function failed restore verification.")
        self._flush_instruction_cache(hook_address, len(original))
        cave = self.sequence_hook_allocation
        self.sequence_hook_original = b""
        self.sequence_hook_allocation = 0
        self.sequence_hook_source = None
        self.sequence_hook_target = None
        self.sequence_hook_recovered = False
        self.sequence_alias_map.clear()
        self.free(cave)
        return True

    @staticmethod
    def build_always_trainable_callback() -> bytes:
        return ALWAYS_TRAINABLE_CALLBACK

    def _install_always_trainable_callback(self, target_id: int) -> int:
        existing = int(self.training_callback_allocations.get(int(target_id), 0))
        if existing and self.process.read(existing, len(ALWAYS_TRAINABLE_CALLBACK)) == ALWAYS_TRAINABLE_CALLBACK:
            return existing
        allocation = self.allocate_executable(0x1000)
        if not self.process.write_protected(allocation, ALWAYS_TRAINABLE_CALLBACK):
            self.free(allocation)
            raise RuntimeError("Could not write the custom unit training callback.")
        if self.process.read(allocation, len(ALWAYS_TRAINABLE_CALLBACK)) != ALWAYS_TRAINABLE_CALLBACK:
            self.free(allocation)
            raise RuntimeError("The custom unit training callback failed verification.")
        self._flush_instruction_cache(allocation, len(ALWAYS_TRAINABLE_CALLBACK))
        old = self.training_callback_allocations.get(int(target_id))
        self.training_callback_allocations[int(target_id)] = allocation
        if old and old != allocation:
            self.free(old)
        return allocation

    def install_training_metadata(
        self, source_id: int, target_id: int, producer_id: int | None = None
    ) -> tuple[int, int, int, int]:
        """Install a donor producer and a stable custom eligibility callback.

        The native Archer callback is not a generic "can train a man" test: it
        intentionally turns false after the Ranger upgrade.  Copying that
        callback made the forged button visible but caused bldg_build_start to
        return zero in otherwise valid Barracks.  The target now receives a tiny
        executable callback that always returns true; costs, unit cap, producer,
        queue state and completion remain enforced by bldg_build_start itself.
        """
        source_id = int(source_id)
        target_id = int(target_id)
        if not 0 <= source_id < TRAINABLE_UNIT_LIMIT:
            raise ValueError(f"Training donor Unit {source_id} is outside 0-{TRAINABLE_UNIT_LIMIT - 1}.")
        if not 0 <= target_id < TRAINABLE_UNIT_LIMIT:
            raise ValueError(
                f"Target Unit {target_id} cannot use bldg_build_man; supported trainable IDs are "
                f"0-{TRAINABLE_UNIT_LIMIT - 1}."
            )
        producer_base = self.process.base + TRAINING_PRODUCER_TABLE_RVA
        callback_base = self.process.base + TRAINING_ELIGIBILITY_TABLE_RVA
        source_producer = self.process.read_u8(producer_base + source_id)
        source_callback = self.process.read_u32(callback_base + source_id * 4)
        target_producer = self.process.read_u8(producer_base + target_id)
        target_callback = self.process.read_u32(callback_base + target_id * 4)
        if source_producer is None or target_producer is None or source_callback is None or target_callback is None:
            raise RuntimeError("Could not read the remaster training metadata tables.")
        module_start = self.process.base
        module_end = module_start + self.process.size
        if producer_id is None:
            producer_id = int(source_producer)
        producer_id = int(producer_id)
        if producer_id == 0x6E:
            raise RuntimeError(f"Unit {source_id} has no native producer assignment.")
        if not 0 <= producer_id <= 0xFF:
            raise ValueError("Producer card ID must fit in one byte.")
        if not module_start <= int(source_callback) < module_end:
            raise RuntimeError(
                f"Unit {source_id} has an invalid training eligibility callback 0x{int(source_callback):08X}."
            )
        self.training_metadata_snapshots.setdefault(target_id, (int(target_producer), int(target_callback)))
        custom_callback = self._install_always_trainable_callback(target_id)
        producer_address = producer_base + target_id
        callback_address = callback_base + target_id * 4
        if not self.process.write_protected(producer_address, bytes([producer_id])):
            raise RuntimeError("Could not write the target producer metadata byte.")
        if not self.process.write_protected(callback_address, struct.pack("<I", int(custom_callback))):
            self.process.write_protected(producer_address, bytes([int(target_producer)]))
            raise RuntimeError("Could not write the custom unit training eligibility callback.")
        if self.process.read_u8(producer_address) != producer_id:
            raise RuntimeError("The target producer metadata failed verification.")
        if self.process.read_u32(callback_address) != int(custom_callback):
            raise RuntimeError("The target training eligibility callback failed verification.")
        return producer_id, int(custom_callback), producer_address, callback_address

    def restore_training_metadata(self) -> int:
        restored = 0
        producer_base = self.process.base + TRAINING_PRODUCER_TABLE_RVA
        callback_base = self.process.base + TRAINING_ELIGIBILITY_TABLE_RVA
        for target_id, (producer, callback) in list(self.training_metadata_snapshots.items()):
            producer_address = producer_base + int(target_id)
            callback_address = callback_base + int(target_id) * 4
            if not self.process.write_protected(producer_address, bytes([int(producer) & 0xFF])):
                raise RuntimeError(f"Could not restore Unit {target_id} producer metadata.")
            if not self.process.write_protected(callback_address, struct.pack("<I", int(callback))):
                raise RuntimeError(f"Could not restore Unit {target_id} training callback.")
            if self.process.read_u8(producer_address) != int(producer):
                raise RuntimeError(f"Unit {target_id} producer metadata restore failed verification.")
            if self.process.read_u32(callback_address) != int(callback):
                raise RuntimeError(f"Unit {target_id} training callback restore failed verification.")
            allocation = self.training_callback_allocations.pop(int(target_id), 0)
            if allocation:
                self.free(allocation)
            restored += 1
        self.training_metadata_snapshots.clear()
        return restored

    def install_sound_aliases(self, source_id: int, target_id: int) -> tuple[int, int, int, int]:
        """Clone remaster-only unit voice and completed-unit sound identity."""
        source_id = int(source_id)
        target_id = int(target_id)
        if not 0 <= source_id < self.card_count or not 0 <= target_id < self.card_count:
            raise ValueError("Sound aliases require valid unit IDs.")
        voice_base = self.process.base + UNIT_VOICE_CALLBACK_TABLE_RVA
        create_base = self.process.base + UNIT_CREATE_SOUND_TABLE_RVA
        source_voice = self.process.read(voice_base + source_id * UNIT_VOICE_CALLBACK_SIZE, UNIT_VOICE_CALLBACK_SIZE)
        target_voice = self.process.read(voice_base + target_id * UNIT_VOICE_CALLBACK_SIZE, UNIT_VOICE_CALLBACK_SIZE)
        source_create = self.process.read(create_base + source_id * UNIT_CREATE_SOUND_SIZE, UNIT_CREATE_SOUND_SIZE)
        target_create = self.process.read(create_base + target_id * UNIT_CREATE_SOUND_SIZE, UNIT_CREATE_SOUND_SIZE)
        if not all((len(source_voice) == 4, len(target_voice) == 4, len(source_create) == 2, len(target_create) == 2)):
            raise RuntimeError("Could not read the remaster unit sound identity tables.")
        source_voice_ptr = struct.unpack("<I", source_voice)[0]
        if not self.process.base <= source_voice_ptr < self.process.base + self.process.size:
            raise RuntimeError(f"Unit {source_id} voice callback 0x{source_voice_ptr:08X} is outside Warcraft II.exe.")
        self.sound_alias_snapshots.setdefault(target_id, (target_voice, target_create))
        voice_address = voice_base + target_id * UNIT_VOICE_CALLBACK_SIZE
        create_address = create_base + target_id * UNIT_CREATE_SOUND_SIZE
        if not self.process.write_protected(voice_address, source_voice):
            raise RuntimeError("Could not write the target unit voice callback.")
        if not self.process.write_protected(create_address, source_create):
            self.process.write_protected(voice_address, target_voice)
            raise RuntimeError("Could not write the target completed-unit sound.")
        if self.process.read(voice_address, 4) != source_voice or self.process.read(create_address, 2) != source_create:
            raise RuntimeError("The target unit sound aliases failed verification.")
        return source_voice_ptr, struct.unpack("<H", source_create)[0], voice_address, create_address

    def restore_sound_aliases(self) -> int:
        restored = 0
        voice_base = self.process.base + UNIT_VOICE_CALLBACK_TABLE_RVA
        create_base = self.process.base + UNIT_CREATE_SOUND_TABLE_RVA
        for target_id, (voice, create) in list(self.sound_alias_snapshots.items()):
            voice_address = voice_base + int(target_id) * UNIT_VOICE_CALLBACK_SIZE
            create_address = create_base + int(target_id) * UNIT_CREATE_SOUND_SIZE
            if not self.process.write_protected(voice_address, voice):
                raise RuntimeError(f"Could not restore Unit {target_id} voice callback.")
            if not self.process.write_protected(create_address, create):
                raise RuntimeError(f"Could not restore Unit {target_id} completed-unit sound.")
            if self.process.read(voice_address, 4) != voice or self.process.read(create_address, 2) != create:
                raise RuntimeError(f"Unit {target_id} sound identity restore failed verification.")
            restored += 1
        self.sound_alias_snapshots.clear()
        return restored

    def install_sequence_table_alias(self, source_id: int, target_id: int) -> tuple[int, int, int, int]:
        """Alias the packed action-sequence row itself, not only one caller.

        The first NUM_UNIT_TYPES words in the loaded sequence table are offsets
        to each unit's action rows.  Copying the donor word makes every caller,
        including death paths that bypass unit_set_sequence, use the donor's
        action animations.
        """
        source_id = int(source_id)
        target_id = int(target_id)
        if not 0 <= source_id < self.card_count or not 0 <= target_id < self.card_count:
            raise ValueError("Sequence-table aliases require valid unit IDs.")
        pointer_address = self.process.base + UNIT_SEQUENCE_TABLE_RVA
        table_pointer = self.process.read_u32(pointer_address) or 0
        if not table_pointer:
            raise RuntimeError("The live unit action-sequence table pointer is null.")
        source_address = table_pointer + source_id * 2
        target_address = table_pointer + target_id * 2
        source_raw = self.process.read(source_address, 2)
        target_raw = self.process.read(target_address, 2)
        if len(source_raw) != 2 or len(target_raw) != 2:
            raise RuntimeError("Could not read the live unit action-sequence rows.")
        source_word = struct.unpack("<H", source_raw)[0]
        target_word = struct.unpack("<H", target_raw)[0]
        if not source_word or source_word & 1:
            raise RuntimeError(
                f"Unit {source_id} has an invalid action-sequence row word 0x{source_word:04X}."
            )
        self.sequence_table_snapshots.setdefault(target_id, (target_address, target_word))
        if target_word != source_word:
            if not self.process.write_protected(target_address, source_raw):
                raise RuntimeError("Could not alias the target unit action-sequence row.")
            if self.process.read(target_address, 2) != source_raw:
                raise RuntimeError("The target unit action-sequence row failed verification.")
        return table_pointer, source_word, target_word, target_address

    def restore_sequence_table_aliases(self) -> int:
        restored = 0
        for target_id, (address, original_word) in list(self.sequence_table_snapshots.items()):
            raw = struct.pack("<H", int(original_word))
            if not self.process.write_protected(int(address), raw):
                raise RuntimeError(f"Could not restore Unit {target_id} action-sequence row.")
            if self.process.read(int(address), 2) != raw:
                raise RuntimeError(f"Unit {target_id} action-sequence row restore failed verification.")
            restored += 1
        self.sequence_table_snapshots.clear()
        return restored

    def allocate(self, size: int) -> int:
        if not kernel32:
            raise RuntimeError("Live command-card allocation is only available on Windows.")
        address = kernel32.VirtualAllocEx(
            self.process.handle,
            None,
            int(size),
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        value = int(address or 0)
        if not value:
            raise RuntimeError("VirtualAllocEx failed.")
        if value > 0xFFFFFFFF:
            kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(value), 0, MEM_RELEASE)
            raise RuntimeError("The command-card allocation was outside the x86 address space.")
        return value

    def free(self, address: int) -> bool:
        if not address:
            return True
        if not kernel32:
            return False
        return bool(kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(address), 0, MEM_RELEASE))

    def apply_raw(self, card_id: int, records: list[bytes]) -> tuple[int, int]:
        for raw in records:
            if len(raw) != RECORD_SIZE:
                raise ValueError("Every command button must be exactly 24 bytes.")
        count = len(records)
        data = b"".join(records)
        allocation = 0
        if count:
            allocation = self.allocate(len(data))
            if not self.process.write_protected(allocation, data):
                self.free(allocation)
                raise RuntimeError("Could not write the new command-card array.")
            if self.process.read(allocation, len(data)) != data:
                self.free(allocation)
                raise RuntimeError("The command-card array failed verification.")

        current = self.read_descriptor(card_id)
        descriptor = self.pack_descriptor(count, allocation, current[2:4])
        if not self.process.write_protected(self.descriptor_address(card_id), descriptor):
            self.free(allocation)
            raise RuntimeError("Could not update the command-card descriptor.")
        if self.read_descriptor(card_id) != descriptor:
            self.free(allocation)
            raise RuntimeError("The command-card descriptor failed verification.")
        self.mark_card_dirty()

        previous = self.allocations.get(card_id, 0)
        self.allocations[card_id] = allocation
        if previous and previous != allocation:
            self.free(previous)
        return count, allocation

    def restore_attach_state(self, card_id: int) -> None:
        descriptor = self.attach_descriptors[card_id]
        if not self.process.write_protected(self.descriptor_address(card_id), descriptor):
            raise RuntimeError(f"Could not restore card 0x{card_id:02X}.")
        if self.read_descriptor(card_id) != descriptor:
            raise RuntimeError(f"Card 0x{card_id:02X} restore failed verification.")
        self.mark_card_dirty()
        allocation = self.allocations.pop(card_id, 0)
        if allocation:
            self.free(allocation)

    def close_without_freeing_active_arrays(self) -> None:
        # Active descriptors point at these allocations. They remain valid until
        # Warcraft exits or the cards are restored.
        self.allocations.clear()
        self.port_snapshots.clear()
        self.group_hook_original = b""
        self.group_hook_allocation = 0
        self.group_hook_source = None
        self.group_hook_target = None
        self.group_hook_dynamic_class_bits = None
        self.group_hook_recovered = False
        self.sequence_hook_original = b""
        self.sequence_hook_allocation = 0
        self.sequence_hook_source = None
        self.sequence_hook_target = None
        self.sequence_hook_recovered = False
        self.sequence_alias_map: dict[int, int] = {}
        self.name_table_original_pointer = 0
        self.name_table_allocation = 0
        self.name_table_size = 0
        self.custom_name = None
        self.training_metadata_snapshots.clear()
        self.sequence_table_snapshots.clear()


class CardService:
    def __init__(self, root: Path):
        mapping = root / "card_data" / "War2_Remaster_Command_Cards_Final.json"
        reference = root / "card_data" / "War2_OLDSB_Card_Reference.json"
        self.process = backend.ProcessMemory()
        self.database = backend.CardDatabase(mapping, reference)
        self.runtime: RuntimeCards | None = None

    def attach(self, progress: Callable[[str], None] | None = None) -> RuntimeCards:
        self.process.attach()
        runtime = RuntimeCards(self.process, self.database)
        runtime.locate_descriptor_table(progress=progress)
        runtime.capture_descriptors(progress=progress)
        self.runtime = runtime
        return runtime

    def compatibility_bytes(self) -> bytes:
        if not self.process.handle:
            return b""
        return self.process.read(self.process.base + PRODUCER_MISMATCH_BRANCH_RVA, 2)

    def set_compatibility(self, enabled: bool) -> None:
        if not self.process.handle:
            raise RuntimeError("Attach to Warcraft II first.")
        address = self.process.base + PRODUCER_MISMATCH_BRANCH_RVA
        current = self.process.read(address, 2)
        expected = PRODUCER_MISMATCH_ORIGINAL if enabled else PRODUCER_MISMATCH_PATCH
        target = PRODUCER_MISMATCH_PATCH if enabled else PRODUCER_MISMATCH_ORIGINAL
        if current == target:
            return
        if current != expected:
            raise RuntimeError(
                f"Producer check at RVA 0x{PRODUCER_MISMATCH_BRANCH_RVA:X} has unexpected bytes "
                f"{current.hex(' ').upper()}; expected {expected.hex(' ').upper()}."
            )
        if not self.process.write_protected(address, target):
            raise RuntimeError("Could not change producer compatibility.")
        if self.process.read(address, 2) != target:
            raise RuntimeError("Producer compatibility write failed verification.")

    def close(self) -> None:
        if self.runtime:
            self.runtime.close_without_freeing_active_arrays()
        self.process.close()
        self.runtime = None
