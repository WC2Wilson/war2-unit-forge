from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Callable

from .profile import (
    PREFERRED_IMAGE_BASE,
    UNITDATA_DESCRIPTOR_COUNT,
    UNITDATA_DESCRIPTOR_RVA,
)
from .war2dat.schemas import detect_views

TYPE_SIZES = {
    "u8": 1,
    "s8": 1,
    "u16": 2,
    "s16": 2,
    "u32": 4,
    "s32": 4,
}
TYPE_FORMATS = {
    "u8": "<B",
    "s8": "<b",
    "u16": "<H",
    "s16": "<h",
    "u32": "<I",
    "s32": "<i",
}


# Warcraft II Remastered keeps a source-backed loader descriptor table in the
# executable. Each entry is {destination pointer, element size, element count}.
# Both supported builds use this exact 32-entry table at RVA 0x004C0150.
#
# This is substantially safer than scanning the live module for DAT bytes. The
# game intentionally transforms two arrays after loading:
#   * gwBuildGroup has 110 added to every value.
#   * gUnitUnmaskTbl replaces small DAT indices with live lookup values.
# Consequently those arrays can never be found by a raw full-array DAT match.
UNITDATA_DESCRIPTOR_RVAS = (
    0x00517128,  # 00 gwBuildGroup
    0x00517208,  # 01 gwLoadAlwaysTbl
    0x00517308,  # 02 gwSummerLoadTbl
    0x00517408,  # 03 gwSnowLoadTbl
    0x00517508,  # 04 gwSwampLoadTbl
    0x00517608,  # 05 gUnitUnmaskTbl
    0x005177C0,  # 06 gwUnitHPTbl
    0x005178A0,  # 07 gbUnitMPTbl
    0x00517910,  # 08 gbUnitStepsCostTbl
    0x00517980,  # 09 gbUnitStoneCostTbl
    0x005179F0,  # 10 gbUnitLumberCostTbl
    0x00517A60,  # 11 gbUnitOilCostTbl
    0x00517AD0,  # 12 gUnitMtxSizeTbl (220 int16 values / 110 x,y pairs)
    0x00517C88,  # 13 gUnitSelectSizeTbl (220 int16 values / 110 x,y pairs)
    0x00517E40,  # 14 gbUnitMtxRangeTbl
    0x00517EB0,  # 15 gbUnitCompRangeTbl
    0x00517F20,  # 16 gbUnitPlayerRangeTbl
    0x00517F90,  # 17 gbUnitArmorTbl
    0x00518000,  # 18 gbMultiSelectTbl
    0x00518070,  # 19 gbKillPriorityTbl
    0x005180E0,  # 20 gbUnitStrengthTbl
    0x00518150,  # 21 gbUnitPierceTbl
    0x005181C0,  # 22 gbUnitHasAttackUpgradeTbl
    0x00518230,  # 23 gbUnitHasArmorUpgradeTbl
    0x005182A0,  # 24 gbUnitBulletTbl
    0x00518310,  # 25 gbUnitClassTbl
    0x00518380,  # 26 gbUnitDecayTbl
    0x005183F0,  # 27 gbRepairPri
    0x00518460,  # 28 gbRClickActionTbl (58 men)
    0x005184A0,  # 29 gwUnitScoreTbl
    0x00518580,  # 30 gbUnitTargetTbl
    0x005185F0,  # 31 gUnitIsTbl
)

UNITDATA_DESCRIPTOR_SHAPES = (
    (2, 110),
    (2, 127),
    (2, 127),
    (2, 127),
    (2, 127),
    (4, 110),
    (2, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (2, 220),
    (2, 220),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 110),
    (1, 58),
    (2, 110),
    (1, 110),
    (4, 110),
)

# source name -> (descriptor index, byte offset within one logical row)
SOURCE_DESCRIPTOR_BINDINGS = {
    "gwBuildGroup": (0, 0),
    "gwLoadAlwaysTbl": (1, 0),
    "gwSummerLoadTbl": (2, 0),
    "gwSnowLoadTbl": (3, 0),
    "gwSwampLoadTbl": (4, 0),
    "gUnitUnmaskTbl": (5, 0),
    "gwUnitHPTbl": (6, 0),
    "gbUnitMPTbl": (7, 0),
    "gbUnitStepsCostTbl": (8, 0),
    "gbUnitStoneCostTbl": (9, 0),
    "gbUnitLumberCostTbl": (10, 0),
    "gbUnitOilCostTbl": (11, 0),
    "gUnitMtxSizeTbl.x": (12, 0),
    "gUnitMtxSizeTbl.y": (12, 2),
    "gUnitSelectSizeTbl.x": (13, 0),
    "gUnitSelectSizeTbl.y": (13, 2),
    "gbUnitMtxRangeTbl": (14, 0),
    "gbUnitCompRangeTbl": (15, 0),
    "gbUnitPlayerRangeTbl": (16, 0),
    "gbUnitArmorTbl": (17, 0),
    "gbMultiSelectTbl": (18, 0),
    "gbKillPriorityTbl": (19, 0),
    "gbUnitStrengthTbl": (20, 0),
    "gbUnitPierceTbl": (21, 0),
    "gbUnitHasAttackUpgradeTbl": (22, 0),
    "gbUnitHasArmorUpgradeTbl": (23, 0),
    "gbUnitBulletTbl": (24, 0),
    "gbUnitClassTbl": (25, 0),
    "gbUnitDecayTbl": (26, 0),
    "gbRepairPri": (27, 0),
    "gbRClickActionTbl": (28, 0),
    "gwUnitScoreTbl": (29, 0),
    "gbUnitTargetTbl": (30, 0),
    "gUnitIsTbl": (31, 0),
}

# Runtime transforms applied by sub_4C4BA0 after unitdata.dat is read.
BUILD_GROUP_RUNTIME_BIAS = 110
UNMASK_LOOKUP_RVA = 0x004C1E28
UNMASK_LOOKUP_COUNT = 10

# Unit graphics are loaded through four 127-entry source-backed file tables,
# then converted into an active pointer table used by the renderer. Unit 34's
# DAT entry is normally 0xFFFF, so cloning only the combat/stat arrays creates
# a unit whose default unitGroup indexes a NULL graphics pointer and crashes the game.
GRAPHICS_LOAD_SOURCES = (
    "gwLoadAlwaysTbl",
    "gwSummerLoadTbl",
    "gwSnowLoadTbl",
    "gwSwampLoadTbl",
)
UNIT_GRAPHICS_POINTER_TABLE_RVA = 0x0051D3B0
UNIT_GRAPHICS_POINTER_COUNT = 127
NO_GRAPHICS_FILE = 0xFFFF
GRAPHICS_ALIAS_BIT = 0x8000


@dataclass(frozen=True)
class ResolvedTable:
    view_name: str
    field_name: str
    source_name: str
    kind: str
    stride: int
    rows: int
    rva: int
    candidate_rvas: tuple[int, ...]
    resolution: str = "loader descriptor"

    @property
    def size(self) -> int:
        return TYPE_SIZES[self.kind]


@dataclass(frozen=True)
class RuntimeWrite:
    source_name: str
    address: int
    before: bytes
    after: bytes


class LiveTableResolver:
    """Resolve and edit DAT-backed unit arrays in live Warcraft memory.

    Supported remaster builds expose the exact loader destination table at
    module RVA 0x004C0150. The resolver reads and validates that table instead
    of searching memory for values that the game has already transformed.
    """

    def __init__(self, process, logger: Callable[[str], None] | None = None):
        self.process = process
        self.log = logger or (lambda _text: None)
        self.tables: dict[str, ResolvedTable] = {}
        self.snapshots: dict[tuple[str, int], bytes] = {}
        self.graphics_pointer_snapshots: dict[int, bytes] = {}
        self.dat_path: Path | None = None
        self.dat_bytes = b""
        self.descriptor_rvas: tuple[int, ...] = ()

    def _pointer_to_rva(self, pointer: int) -> int:
        base = int(self.process.base)
        size = int(self.process.size)
        if base <= pointer < base + size:
            return pointer - base
        # A static/offline test image can expose preferred-image pointers.
        if PREFERRED_IMAGE_BASE <= pointer < PREFERRED_IMAGE_BASE + size:
            return pointer - PREFERRED_IMAGE_BASE
        raise RuntimeError(
            f"Unit-data descriptor pointer 0x{pointer:08X} is outside the Warcraft II module "
            f"(base 0x{base:08X}, size 0x{size:X})."
        )

    def _read_descriptors(self) -> list[tuple[int, int, int]]:
        size = UNITDATA_DESCRIPTOR_COUNT * 12
        raw = self.process.read(self.process.base + UNITDATA_DESCRIPTOR_RVA, size)
        if len(raw) != size:
            raise RuntimeError(
                f"Could not read the {UNITDATA_DESCRIPTOR_COUNT}-entry unit-data loader table "
                f"at RVA 0x{UNITDATA_DESCRIPTOR_RVA:08X}."
            )

        entries: list[tuple[int, int, int]] = []
        normalized: list[int] = []
        for index in range(UNITDATA_DESCRIPTOR_COUNT):
            pointer, element_size, element_count = struct.unpack_from("<III", raw, index * 12)
            expected_size, expected_count = UNITDATA_DESCRIPTOR_SHAPES[index]
            if (element_size, element_count) != (expected_size, expected_count):
                raise RuntimeError(
                    f"Unit-data descriptor {index} has shape {element_size} x {element_count}; "
                    f"expected {expected_size} x {expected_count}. The running build is not a "
                    "verified Unit Forge profile."
                )
            rva = self._pointer_to_rva(pointer)
            expected_rva = UNITDATA_DESCRIPTOR_RVAS[index]
            if rva != expected_rva:
                raise RuntimeError(
                    f"Unit-data descriptor {index} points to RVA 0x{rva:08X}; expected "
                    f"0x{expected_rva:08X}. Refusing an unverified live write."
                )
            entries.append((pointer, element_size, element_count))
            normalized.append(rva)
        self.descriptor_rvas = tuple(normalized)
        return entries

    def resolve(self, dat_path: str | Path) -> dict[str, ResolvedTable]:
        self.dat_path = Path(dat_path)
        self.dat_bytes = self.dat_path.read_bytes()
        views = detect_views(self.dat_path, len(self.dat_bytes))
        if not views:
            raise RuntimeError(f"Unsupported unit DAT format: {self.dat_path} ({len(self.dat_bytes)} bytes)")

        entries = self._read_descriptors()
        fields = {}
        for view in views:
            if view.name not in {"Units", "Unit Groups", "Men Right-click"}:
                continue
            for field in view.fields:
                # The optional Orc-swamp table exists only in the larger DAT
                # variant and is not represented by the verified 32-entry PC
                # loader descriptor table. It is still cloned persistently.
                if field.source == "gwOrcSwampLoadTbl":
                    continue
                fields[field.source] = (view, field)

        missing = sorted(set(fields) - set(SOURCE_DESCRIPTOR_BINDINGS))
        if missing:
            raise RuntimeError("No source-backed descriptor binding for: " + ", ".join(missing))

        resolved: dict[str, ResolvedTable] = {}
        for source_name, (view, field) in fields.items():
            descriptor_index, row_offset = SOURCE_DESCRIPTOR_BINDINGS[source_name]
            _pointer, element_size, element_count = entries[descriptor_index]
            base_rva = self.descriptor_rvas[descriptor_index]
            rows = len(view.rows)
            if TYPE_SIZES[field.kind] != element_size:
                raise RuntimeError(
                    f"{source_name} type width {TYPE_SIZES[field.kind]} does not match loader width {element_size}."
                )
            # x/y tables contain 220 int16 values but are exposed as 110 rows
            # with a four-byte logical stride.
            if field.stride == element_size:
                logical_rows = element_count
            else:
                logical_rows = element_count * element_size // field.stride
            if rows != logical_rows:
                raise RuntimeError(
                    f"{source_name} has {rows} schema rows but loader descriptor implies {logical_rows}."
                )
            rva = base_rva + row_offset
            end_rva = rva + (rows - 1) * field.stride + TYPE_SIZES[field.kind]
            if end_rva > self.process.size:
                raise RuntimeError(f"{source_name} extends outside the live module.")
            table = ResolvedTable(
                view.name,
                field.name,
                source_name,
                field.kind,
                field.stride,
                rows,
                rva,
                (rva,),
            )
            resolved[source_name] = table
            self.log(
                f"Resolved {source_name}: RVA 0x{rva:08X} "
                f"(loader descriptor {descriptor_index}, {element_size} x {element_count})"
            )

        self.tables = resolved
        self.log(
            f"Validated source-backed unit-data loader table at RVA 0x{UNITDATA_DESCRIPTOR_RVA:08X}; "
            f"resolved {len(resolved)} editable arrays without pattern scanning."
        )
        return resolved

    def read_value(self, source_name: str, row: int) -> int:
        table = self.tables[source_name]
        if not 0 <= row < table.rows:
            raise IndexError(row)
        raw = self.process.read(
            self.process.base + table.rva + row * table.stride,
            table.size,
        )
        if len(raw) != table.size:
            raise RuntimeError(f"Could not read {source_name}[{row}].")
        return struct.unpack(TYPE_FORMATS[table.kind], raw)[0]

    def _runtime_override_value(self, source_name: str, value: int) -> int:
        """Convert a DAT reference value into the game's post-load runtime value."""
        if source_name == "gwBuildGroup":
            return int(value) + BUILD_GROUP_RUNTIME_BIAS
        if source_name == "gUnitUnmaskTbl":
            index = int(value)
            if not 0 <= index < UNMASK_LOOKUP_COUNT:
                raise ValueError(
                    f"gUnitUnmaskTbl DAT value {index} is outside the verified lookup range "
                    f"0-{UNMASK_LOOKUP_COUNT - 1}."
                )
            raw = self.process.read(self.process.base + UNMASK_LOOKUP_RVA + index * 4, 4)
            if len(raw) != 4:
                raise RuntimeError("Could not read the live unit-unmask lookup table.")
            return struct.unpack("<I", raw)[0]
        return int(value)

    def write_value(self, source_name: str, row: int, value: int) -> RuntimeWrite:
        table = self.tables[source_name]
        if not 0 <= row < table.rows:
            raise IndexError(row)
        after = struct.pack(TYPE_FORMATS[table.kind], int(value))
        address = self.process.base + table.rva + row * table.stride
        before = self.process.read(address, table.size)
        if len(before) != table.size:
            raise RuntimeError(f"Could not read target {source_name}[{row}].")
        self.snapshots.setdefault((source_name, row), before)
        if before != after:
            if not self.process.write_protected(address, after):
                raise RuntimeError(f"Could not write {source_name}[{row}] at 0x{address:08X}.")
            verified = self.process.read(address, table.size)
            if verified != after:
                raise RuntimeError(f"Verification failed for {source_name}[{row}].")
        return RuntimeWrite(source_name, address, before, after)

    def clone_row(
        self,
        source_row: int,
        target_row: int,
        overrides: dict[str, int] | None = None,
        verify: bool = True,
    ) -> list[RuntimeWrite]:
        overrides = overrides or {}
        writes: list[RuntimeWrite] = []
        for source_name, table in self.tables.items():
            # Graphics load entries need alias semantics and an active pointer
            # update; raw copying can make the game free one allocation twice.
            if source_name in GRAPHICS_LOAD_SOURCES:
                continue
            if source_row >= table.rows or target_row >= table.rows:
                continue
            if source_name in overrides:
                value = self._runtime_override_value(source_name, overrides[source_name])
            else:
                # Copy the already-transformed live value. This preserves the
                # exact runtime representation for build groups and unmask data.
                value = self.read_value(source_name, source_row)
            writes.append(self.write_value(source_name, target_row, value))
        if verify:
            mismatches: list[str] = []
            for source_name, table in self.tables.items():
                if source_name in GRAPHICS_LOAD_SOURCES:
                    continue
                if source_row >= table.rows or target_row >= table.rows:
                    continue
                if source_name in overrides:
                    expected = self._runtime_override_value(source_name, overrides[source_name])
                else:
                    expected = self.read_value(source_name, source_row)
                actual = self.read_value(source_name, target_row)
                if actual != expected:
                    mismatches.append(f"{source_name}: expected {expected}, got {actual}")
            if mismatches:
                raise RuntimeError(
                    f"Live Unit {target_row} row verification failed after cloning Unit {source_row}: "
                    + "; ".join(mismatches[:8])
                )
        return writes

    def install_graphics_alias(self, source_row: int, target_row: int) -> list[RuntimeWrite]:
        """Give the target unit-group slot a safe live alias to source graphics.

        The game's graphics loader uses bit 15 to mark a table entry as an
        alias to a previously loaded graphics resource. The active renderer,
        however, reads a separate pointer table, so a live install must update
        both representations. This prevents the NULL dereference seen when
        Unit 34 was created with its default 0xFFFF graphics entry.
        """
        if not 0 <= source_row < UNIT_GRAPHICS_POINTER_COUNT:
            raise IndexError(source_row)
        if not 0 <= target_row < UNIT_GRAPHICS_POINTER_COUNT:
            raise IndexError(target_row)
        if source_row == target_row:
            return []
        if source_row > target_row:
            raise RuntimeError(
                "Live graphics aliasing requires the source graphics group to precede "
                "the target group. Build persistent DAT files and restart Warcraft for "
                "a source group after the target."
            )

        writes: list[RuntimeWrite] = []
        for source_name in GRAPHICS_LOAD_SOURCES:
            table = self.tables.get(source_name)
            if table is None:
                raise RuntimeError(f"Live graphics table {source_name} was not resolved.")
            source_value = self.read_value(source_name, source_row)
            # 0xFFFF already means no file. Valid source file numbers receive
            # the high-bit alias marker so cleanup does not free them twice.
            alias_value = (source_value | GRAPHICS_ALIAS_BIT) if source_value != NO_GRAPHICS_FILE else NO_GRAPHICS_FILE
            writes.append(self.write_value(source_name, target_row, alias_value))

        source_address = self.process.base + UNIT_GRAPHICS_POINTER_TABLE_RVA + source_row * 4
        target_address = self.process.base + UNIT_GRAPHICS_POINTER_TABLE_RVA + target_row * 4
        source_raw = self.process.read(source_address, 4)
        target_raw = self.process.read(target_address, 4)
        if len(source_raw) != 4 or len(target_raw) != 4:
            raise RuntimeError("Could not read the active unit-graphics pointer table.")
        source_pointer = struct.unpack("<I", source_raw)[0]
        if source_pointer == 0:
            raise RuntimeError(
                f"Source graphics group {source_row} has a NULL active graphics pointer. "
                "Enter a loaded match before installing the live preset."
            )
        # Validate that the source pointer is readable without assuming it lies
        # inside the main executable module.
        if len(self.process.read(source_pointer, 4)) != 4:
            raise RuntimeError(
                f"Source graphics pointer 0x{source_pointer:08X} is not readable; refusing a live alias."
            )
        self.graphics_pointer_snapshots.setdefault(target_row, target_raw)
        if target_raw != source_raw:
            if not self.process.write_protected(target_address, source_raw):
                raise RuntimeError(
                    f"Could not alias graphics pointer table entry {target_row} at 0x{target_address:08X}."
                )
            if self.process.read(target_address, 4) != source_raw:
                raise RuntimeError(f"Graphics pointer alias verification failed for group {target_row}.")
        writes.append(RuntimeWrite("active_unit_graphics_pointer", target_address, target_raw, source_raw))
        self.log(
            f"Aliased live graphics group {target_row} to source group {source_row}: "
            f"active pointer 0x{source_pointer:08X}; loader entries use verified high-bit alias semantics."
        )
        return writes

    def restore(self) -> int:
        restored = 0
        for target_row, raw in list(self.graphics_pointer_snapshots.items())[::-1]:
            address = self.process.base + UNIT_GRAPHICS_POINTER_TABLE_RVA + target_row * 4
            if not self.process.write_protected(address, raw):
                raise RuntimeError(f"Could not restore active graphics pointer entry {target_row}.")
            if self.process.read(address, len(raw)) != raw:
                raise RuntimeError(f"Graphics pointer restore verification failed for entry {target_row}.")
            restored += 1
        self.graphics_pointer_snapshots.clear()
        for (source_name, row), raw in list(self.snapshots.items())[::-1]:
            table = self.tables.get(source_name)
            if table is None:
                continue
            address = self.process.base + table.rva + row * table.stride
            if not self.process.write_protected(address, raw):
                raise RuntimeError(f"Could not restore {source_name}[{row}].")
            if self.process.read(address, len(raw)) != raw:
                raise RuntimeError(f"Restore verification failed for {source_name}[{row}].")
            restored += 1
        self.snapshots.clear()
        return restored
