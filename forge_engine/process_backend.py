from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional
import xml.etree.ElementTree as ET
from xml.dom import minidom


APP_TITLE = "Warcraft II Remaster - Command Card Studio v2.1.1"
PROCESS_NAME = "Warcraft II.exe"

SELECTED_OBJECT_POINTER_RVA = 0x0051CC40
SELECTED_CARD_ID_OFFSET = 0x25

RECORD_SIZE = 24

# Confirmed unit-training producer compatibility gate.
# Original code at Warcraft II.exe+AC873 is: 75 29 (JNE reject).
# Replacing it with 90 90 allows a copied bldg_build_man button to train
# from a different producer type while leaving the remaining validation,
# costs, queue, and unit-specific callbacks intact.
PRODUCER_TABLE_RVA = 0x00438248
PRODUCER_MISMATCH_BRANCH_RVA = 0x000AC873
PRODUCER_MISMATCH_ORIGINAL = b"\x75\x29"
PRODUCER_MISMATCH_PATCH = b"\x90\x90"

CLONE_MODES = (
    "Functional replacement (keep target slot)",
    "Full 24-byte record (copy source slot)",
    "Appearance only (icon + tooltip)",
    "Behavior only (keep target slot/icon/tooltip)",
)

FIELD_LAYOUT = (
    ("panel_slot", "Panel Slot", 0x00, "H"),
    ("icon_id", "Icon ID", 0x02, "H"),
    ("visibility_callback", "Visibility Callback", 0x04, "I"),
    ("remaster_bridge", "Remaster Bridge / Dispatch", 0x08, "I"),
    ("action_callback", "Gameplay Action Callback", 0x0C, "I"),
    ("visibility_parameter", "Visibility Parameter", 0x10, "B"),
    ("action_parameter", "Action Parameter", 0x11, "B"),
    ("tooltip_id", "Tooltip / String ID", 0x12, "H"),
    ("targeting_mask", "Targeting / Order Mask", 0x14, "H"),
    ("auxiliary", "Auxiliary", 0x16, "H"),
)

FIELD_INFO = {
    name: {
        "label": label,
        "offset": offset,
        "fmt": fmt,
        "size": struct.calcsize("<" + fmt),
    }
    for name, label, offset, fmt in FIELD_LAYOUT
}

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400

PAGE_EXECUTE_READWRITE = 0x40

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000

PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100

READABLE_PAGE_FLAGS = {
    PAGE_READONLY,
    PAGE_READWRITE,
    PAGE_WRITECOPY,
    PAGE_EXECUTE_READ,
    PAGE_EXECUTE_READWRITE,
    PAGE_EXECUTE_WRITECOPY,
}
WRITABLE_PAGE_FLAGS = {
    PAGE_READWRITE,
    PAGE_WRITECOPY,
    PAGE_EXECUTE_READWRITE,
    PAGE_EXECUTE_WRITECOPY,
}


FRIENDLY_NAMES = {
    "sgHDwarvesCard": "Dwarven Demolition Squad",
    "sgOGoblinsCard": "Goblin Sappers",
    "sgHGruntCard": "Footman / Ballista",
    "sgOGruntCard": "Grunt / Catapult",
    "sgHBallistaCard": "Ballista / Footman",
    "sgOBallistaCard": "Catapult / Grunt",
    "sgHAtkPeonCard": "Attack-only Peasant",
    "sgOAtkPeonCard": "Attack-only Peon",
    "sgHArcherCard": "Archer / Ranger",
    "sgOArcherCard": "Axethrower / Berserker",
    "sgHPeasantCard": "Peasant",
    "sgOPeasantCard": "Peon",
    "sgHumanBldWdnCard": "Human Basic Build Menu",
    "sgOrcBldWdnCard": "Orc Basic Build Menu",
    "sgHumanBldStnCard": "Human Advanced Build Menu",
    "sgOrcBldStnCard": "Orc Advanced Build Menu",
    "sgHWizardCard": "Mage",
    "sgOWizardCard": "Death Knight",
    "sgHKnightCard": "Knight",
    "sgOKnightCard": "Ogre",
    "sgHPaladinCard": "Paladin",
    "sgOOgreMageCard": "Ogre Mage",
    "sgOChogalCard": "Cho'gall",
    "sgHTankerCard": "Human Oil Tanker",
    "sgOTankerCard": "Orc Oil Tanker",
    "sgHSubCard": "Gnomish Submarine",
    "sgOSubCard": "Giant Turtle",
    "sgHDestroyerCard": "Destroyer / Battleship",
    "sgODestroyerCard": "Troll Destroyer / Juggernaut",
    "sgHTransportCard": "Human Transport",
    "sgOTransportCard": "Orc Transport",
    "sgHFlyerCard": "Gnomish Flying Machine",
    "sgOBalloonCard": "Goblin Zeppelin",
    "sgHShipyardCard": "Human Shipyard",
    "sgOShipyardCard": "Orc Shipyard",
    "sgHFoundryCard": "Human Foundry",
    "sgOFoundryCard": "Orc Foundry",
    "sgHMillCard": "Elven Lumber Mill",
    "sgOMillCard": "Troll Lumber Mill",
    "sgHSmithCard": "Human Blacksmith",
    "sgOSmithCard": "Orc Blacksmith",
    "sgHTownHallCard": "Town Hall / Keep / Castle",
    "sgOTownHallCard": "Great Hall / Stronghold / Fortress",
    "sgHAviaryCard": "Gryphon Aviary",
    "sgOAviaryCard": "Dragon Roost",
    "sgHInventerCard": "Gnomish Inventor",
    "sgOInventerCard": "Goblin Alchemist",
    "sgHBarracksCard": "Human Barracks",
    "sgOBarracksCard": "Orc Barracks",
    "sgHTowerCard": "Human Guard / Cannon Tower",
    "sgOTowerCard": "Orc Guard / Cannon Tower",
    "sgHWTowerCard": "Mage Tower",
    "sgOWTowerCard": "Temple of the Damned",
    "sgHChurchCard": "Church",
    "sgOChurchCard": "Altar of Storms",
    "sgCancelTargetCard": "Cancel Targeting",
    "sgCancelPlaceCard": "Cancel Placement",
    "sgCancelBuildCard": "Cancel Construction / Upgrade / Training",
    "sgHGenericCard": "Human Generic Group",
    "sgOGenericCard": "Orc Generic Group",
    "paddata": "Padding / Shared Cancel Record",
}


BUILDING_TOKENS = (
    "Shipyard", "Foundry", "Mill", "Smith", "TownHall", "Aviary",
    "Inventer", "Barracks", "Tower", "Church",
)


if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD),
            ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
            ("modBaseSize", wintypes.DWORD),
            ("hModule", wintypes.HMODULE),
            ("szModule", wintypes.WCHAR * 256),
            ("szExePath", wintypes.WCHAR * 260),
        ]


    if ctypes.sizeof(ctypes.c_void_p) == 8:
        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
            ]
    else:
        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
            ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL

    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL

    kernel32.Module32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32FirstW.restype = wintypes.BOOL

    kernel32.Module32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32NextW.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL

    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL

    kernel32.VirtualProtectEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.VirtualProtectEx.restype = wintypes.BOOL

    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def close_handle(handle) -> None:
    if handle and handle != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(handle)


def find_pid(name: str) -> int:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise RuntimeError("Could not enumerate running processes.")

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)

        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise RuntimeError("Could not read the process list.")

        while True:
            if entry.szExeFile.lower() == name.lower():
                return int(entry.th32ProcessID)

            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)

    raise RuntimeError(f"{name} is not running.")


def find_module(pid: int, name: str) -> tuple[int, int, str]:
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32,
        pid,
    )
    if snapshot == INVALID_HANDLE_VALUE:
        raise RuntimeError("Could not enumerate process modules.")

    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)

        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            raise RuntimeError("Could not read the module list.")

        while True:
            if entry.szModule.lower() == name.lower():
                base = ctypes.cast(
                    entry.modBaseAddr,
                    ctypes.c_void_p,
                ).value or 0
                return int(base), int(entry.modBaseSize), entry.szExePath

            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)

    raise RuntimeError(f"Module {name} was not found.")


class ProcessMemory:
    def __init__(self):
        self.handle = None
        self.pid = 0
        self.base = 0
        self.size = 0
        self.path = ""

    def attach(self) -> None:
        self.close()

        self.pid = find_pid(PROCESS_NAME)
        self.base, self.size, self.path = find_module(
            self.pid,
            PROCESS_NAME,
        )

        access = (
            PROCESS_QUERY_INFORMATION
            | PROCESS_VM_OPERATION
            | PROCESS_VM_READ
            | PROCESS_VM_WRITE
        )
        self.handle = kernel32.OpenProcess(access, False, self.pid)

        if not self.handle:
            raise RuntimeError(
                "OpenProcess failed. Run this editor as Administrator."
            )

    def close(self) -> None:
        if self.handle:
            close_handle(self.handle)
        self.handle = None

    def read(self, address: int, size: int) -> bytes:
        if not self.handle or size <= 0:
            return b""

        buffer = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()

        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(got),
        )
        if not ok or got.value != size:
            return b""

        return buffer.raw

    def read_u8(self, address: int) -> Optional[int]:
        raw = self.read(address, 1)
        return raw[0] if len(raw) == 1 else None

    def read_u32(self, address: int) -> Optional[int]:
        raw = self.read(address, 4)
        return struct.unpack("<I", raw)[0] if len(raw) == 4 else None

    def write_protected(self, address: int, data: bytes) -> bool:
        if not self.handle or not data:
            return False

        old = wintypes.DWORD()
        changed = kernel32.VirtualProtectEx(
            self.handle,
            ctypes.c_void_p(address),
            len(data),
            PAGE_EXECUTE_READWRITE,
            ctypes.byref(old),
        )

        buffer = ctypes.create_string_buffer(data)
        wrote = ctypes.c_size_t()

        ok = kernel32.WriteProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            len(data),
            ctypes.byref(wrote),
        )

        if changed:
            ignored = wintypes.DWORD()
            kernel32.VirtualProtectEx(
                self.handle,
                ctypes.c_void_p(address),
                len(data),
                old.value,
                ctypes.byref(ignored),
            )

        return bool(ok and wrote.value == len(data))

    def iter_regions(self):
        if not self.handle:
            return

        address = 0
        maximum = 0x7FFF0000

        while address < maximum:
            info = MEMORY_BASIC_INFORMATION()
            got = kernel32.VirtualQueryEx(
                self.handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not got:
                break

            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize or 0)
            if size <= 0:
                break

            yield (
                base,
                size,
                int(info.State),
                int(info.Protect),
                int(info.Type),
            )

            next_address = base + size
            if next_address <= address:
                break
            address = next_address

    def find_exact_copies(
        self,
        pattern: bytes,
        *,
        target_alignment: int = 0,
        include_live_private: bool = True,
        max_matches: int = 128,
    ) -> list[int]:
        """Find exact record copies in the module and writable live-memory regions."""
        if not self.handle or not pattern:
            return []

        matches: list[int] = []
        overlap = max(0, len(pattern) - 1)
        chunk_size = 1024 * 1024
        module_start = self.base
        module_end = self.base + self.size

        for base, size, state, protect, region_type in self.iter_regions():
            if state != MEM_COMMIT:
                continue
            if protect & (PAGE_GUARD | PAGE_NOACCESS):
                continue

            basic_protect = protect & 0xFF
            in_module = base < module_end and (base + size) > module_start

            if in_module:
                allowed = basic_protect in READABLE_PAGE_FLAGS
            else:
                allowed = (
                    include_live_private
                    and region_type in {MEM_PRIVATE, MEM_MAPPED}
                    and basic_protect in WRITABLE_PAGE_FLAGS
                )

            if not allowed:
                continue

            region_size = min(size, 256 * 1024 * 1024)
            offset = 0
            tail = b""

            while offset < region_size and len(matches) < max_matches:
                request = min(chunk_size, region_size - offset)
                raw = self.read(base + offset, request)
                if not raw:
                    break

                data = tail + raw
                data_base = base + offset - len(tail)
                position = 0

                while len(matches) < max_matches:
                    found = data.find(pattern, position)
                    if found < 0:
                        break

                    absolute = data_base + found
                    if absolute % 8 == target_alignment % 8:
                        if absolute not in matches:
                            matches.append(absolute)

                    position = found + 1

                tail = data[-overlap:] if overlap else b""
                offset += request

            if len(matches) >= max_matches:
                break

        return sorted(matches)


class CardDatabase:
    def __init__(self, mapping_path: Path, reference_path: Path):
        self.mapping_path = mapping_path
        self.reference_path = reference_path
        self.mapping = {}
        self.reference = {}
        self.effective: dict[str, dict[int, int]] = {}
        self.row_sources: dict[tuple[str, int], str] = {}
        self.reverse: dict[int, list[tuple[str, int]]] = {}
        self.load()

    def load(self) -> None:
        self.mapping = json.loads(
            self.mapping_path.read_text(encoding="utf-8")
        )
        self.reference = json.loads(
            self.reference_path.read_text(encoding="utf-8")
        )
        self.rebuild()

    def load_mapping(self, path: Path) -> None:
        self.mapping_path = path
        self.load()

    def is_legacy_row(self, row: list[str]) -> bool:
        text = " ".join(str(value).upper() for value in row)
        return any(
            token in text
            for token in (
                "AUTO_BUILD",
                "AUTO_UPGRADE",
                "BLDG_AUTO_BUILD",
                "BLDG_AUTO_UPGRADE",
                "CANCEL_AUTO_UPGRADE",
            )
        )

    def rebuild(self) -> None:
        self.effective = {}
        self.row_sources = {}
        self.reverse = {}

        mappings = self.mapping.get("button_mappings", {})
        bases = self.mapping.get("confirmed_card_bases", {})
        skipped = self.mapping.get("skipped_buttons", {})

        for array_name, source_rows in self.reference.get("arrays", {}).items():
            explicit = {
                int(key): int(value)
                for key, value in mappings.get(array_name, {}).items()
            }
            skipped_rows = {
                int(key)
                for key in skipped.get(array_name, {})
            }

            usable_rows = [
                index
                for index, row in enumerate(source_rows)
                if index not in skipped_rows
                and not self.is_legacy_row(row)
            ]

            rows: dict[int, int] = {}

            # Older unit mappings confirmed the array base but only stored row 0.
            # Those original unit/menu arrays remain contiguous in Remaster.
            if (
                set(explicit) == {0}
                and array_name in bases
                and len(usable_rows) > 1
            ):
                base = int(bases[array_name])
                for index in usable_rows:
                    rows[index] = base + index * RECORD_SIZE
                    self.row_sources[(array_name, index)] = "Derived from confirmed base"
            else:
                for index in usable_rows:
                    if index in explicit:
                        rows[index] = explicit[index]
                        self.row_sources[(array_name, index)] = "Explicitly confirmed"

            if rows:
                self.effective[array_name] = rows
                for row_index, rva in rows.items():
                    self.reverse.setdefault(rva, []).append(
                        (array_name, row_index)
                    )

    def source_rows(self, array_name: str) -> list[list[str]]:
        return self.reference.get("arrays", {}).get(array_name, [])

    def card_ids(self, array_name: str) -> list[int]:
        return [
            index
            for index, name in enumerate(
                self.reference.get("cards", [])
            )
            if name == array_name
        ]

    def friendly(self, array_name: str) -> str:
        return FRIENDLY_NAMES.get(array_name, array_name)

    def faction(self, array_name: str) -> str:
        if array_name.startswith(("sgH", "sgHuman")):
            return "Human"
        if array_name.startswith(("sgO", "sgOrc")):
            return "Orc"
        return "Shared"

    def category(self, array_name: str) -> str:
        if array_name.startswith("sgCancel") or array_name == "paddata":
            return "Cancel / Special"
        if "BldWdn" in array_name or "BldStn" in array_name:
            return "Build Menus"
        if any(token in array_name for token in BUILDING_TOKENS):
            return "Buildings"
        if "Generic" in array_name:
            return "Generic / Group"
        return "Units"

    def all_unique_rvas(self) -> list[int]:
        return sorted(self.reverse)

    def aliases(self, rva: int) -> list[str]:
        result = []
        for name, row_index in self.reverse.get(rva, []):
            result.append(
                f"{self.friendly(name)} B{row_index + 1}"
            )
        return result


def classify_source_action(source_row: list[str]) -> str:
    action = str(source_row[3]) if len(source_row) > 3 else ""
    visibility = str(source_row[2]) if len(source_row) > 2 else ""
    text = f"{action} {visibility}".lower()

    if action == "bldg_build_man":
        return "Unit Training"
    if "upgrade" in text or "research" in text:
        return "Upgrade / Research"
    if action == "status_set_card":
        return "Menu Navigation"
    if action.startswith("order_"):
        return "Orders / Spells"
    if "build" in action or "place" in action:
        return "Building / Placement"
    return "Other"


class ButtonLibraryDialog(tk.Toplevel):
    """Searchable picker for every confirmed live button record."""

    def __init__(self, parent: "CommandCardEditor"):
        super().__init__(parent)
        self.parent = parent
        self.db = parent.db
        self.result: Optional[tuple[str, int, int]] = None

        self.title("Choose source button")
        self.geometry("1320x700")
        self.minsize(980, 520)
        self.configure(bg="#1E1E1E")
        self.transient(parent)

        self.search_var = tk.StringVar(value="")
        self.type_var = tk.StringVar(value="All")
        self.faction_var = tk.StringVar(value="All")
        self.summary_var = tk.StringVar(
            value="Double-click a row or select it and click Use Selected Source."
        )

        filters = ttk.Frame(self, padding=8)
        filters.pack(fill=tk.X)

        ttk.Label(filters, text="Search").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(filters, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        search.bind("<KeyRelease>", lambda _event: self.populate())

        ttk.Label(filters, text="Type").grid(row=0, column=2, sticky="w")
        type_combo = ttk.Combobox(
            filters,
            textvariable=self.type_var,
            values=(
                "All",
                "Unit Training",
                "Building / Placement",
                "Upgrade / Research",
                "Orders / Spells",
                "Menu Navigation",
                "Other",
            ),
            state="readonly",
            width=22,
        )
        type_combo.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        type_combo.bind("<<ComboboxSelected>>", lambda _event: self.populate())

        ttk.Label(filters, text="Faction").grid(row=0, column=4, sticky="w")
        faction_combo = ttk.Combobox(
            filters,
            textvariable=self.faction_var,
            values=("All", "Human", "Orc", "Shared"),
            state="readonly",
            width=12,
        )
        faction_combo.grid(row=0, column=5, sticky="ew", padx=(6, 0))
        faction_combo.bind("<<ComboboxSelected>>", lambda _event: self.populate())
        filters.columnconfigure(1, weight=1)

        tree_box = ttk.Frame(self, padding=(8, 0, 8, 8))
        tree_box.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            tree_box,
            columns=(
                "faction", "type", "card", "button", "slot",
                "icon", "action", "parameter", "rva",
            ),
            show="headings",
            selectmode="browse",
        )

        columns = (
            ("faction", "Faction", 70),
            ("type", "Type", 130),
            ("card", "Card / Unit / Building", 285),
            ("button", "Button", 58),
            ("slot", "Slot", 48),
            ("icon", "Source Icon", 145),
            ("action", "Action", 165),
            ("parameter", "Parameter", 120),
            ("rva", "Record RVA", 105),
        )
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(
                key,
                width=width,
                anchor="w" if key in {"card", "icon", "action", "parameter"} else "center",
            )

        yscroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_box, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", lambda _event: self.accept())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_summary())

        footer = ttk.Frame(self, padding=8)
        footer.pack(fill=tk.X)
        ttk.Label(
            footer,
            textvariable=self.summary_var,
            font=("Consolas", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(
            footer,
            text="Use Selected Source",
            command=self.accept,
        ).pack(side=tk.RIGHT)
        ttk.Button(
            footer,
            text="Cancel",
            command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        self.populate()
        search.focus_set()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def row_key(self, array_name: str, row_index: int) -> str:
        return f"{array_name}::{row_index}"

    def parse_key(self, key: str) -> tuple[str, int]:
        array_name, row_text = key.rsplit("::", 1)
        return array_name, int(row_text)

    def populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        search = self.search_var.get().strip().lower()
        selected_type = self.type_var.get()
        selected_faction = self.faction_var.get()

        rows = []
        for array_name, mapped_rows in self.db.effective.items():
            source_rows = self.db.source_rows(array_name)
            faction = self.db.faction(array_name)
            friendly = self.db.friendly(array_name)

            if selected_faction != "All" and faction != selected_faction:
                continue

            for row_index, rva in mapped_rows.items():
                if row_index >= len(source_rows):
                    continue
                source = source_rows[row_index]
                source_type = classify_source_action(source)
                if selected_type != "All" and source_type != selected_type:
                    continue

                haystack = " ".join(
                    [
                        array_name,
                        friendly,
                        faction,
                        source_type,
                        *[str(value) for value in source],
                        f"{rva:08X}",
                    ]
                ).lower()
                if search and search not in haystack:
                    continue

                rows.append(
                    (
                        source_type,
                        faction,
                        friendly,
                        array_name,
                        row_index,
                        rva,
                        source,
                    )
                )

        rows.sort(key=lambda item: (item[0], item[1], item[2], item[4]))

        for source_type, faction, friendly, array_name, row_index, rva, source in rows:
            key = self.row_key(array_name, row_index)
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    faction,
                    source_type,
                    f"{friendly} [{array_name}]",
                    row_index + 1,
                    source[0] if len(source) > 0 else "—",
                    source[1] if len(source) > 1 else "—",
                    source[3] if len(source) > 3 else "—",
                    source[5] if len(source) > 5 else "—",
                    f"+0x{rva:08X}",
                ),
            )

        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self.update_summary()
        else:
            self.summary_var.set("No confirmed buttons match the current filters.")

    def update_summary(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        array_name, row_index = self.parse_key(selection[0])
        source = self.db.source_rows(array_name)[row_index]
        rva = self.db.effective[array_name][row_index]
        self.summary_var.set(
            f"{self.db.friendly(array_name)} | Button {row_index + 1} | "
            f"Slot {source[0]} | {source[1]} | {source[3]} | "
            f"Parameter {source[5]} | +0x{rva:08X}"
        )

    def accept(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning(APP_TITLE, "Select a source button first.", parent=self)
            return
        array_name, row_index = self.parse_key(selection[0])
        self.result = (
            array_name,
            row_index,
            self.db.effective[array_name][row_index],
        )
        self.destroy()


class CommandCardEditor(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = min(1780, max(1180, screen_width - 40))
        window_height = min(980, max(720, screen_height - 80))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(1180, 720)

        app_dir = Path(__file__).resolve().parent
        self.mapping_path = app_dir / "War2_Remaster_Command_Cards_Final.json"
        self.reference_path = app_dir / "War2_OLDSB_Card_Reference.json"

        if not self.mapping_path.exists() or not self.reference_path.exists():
            messagebox.showerror(
                APP_TITLE,
                "The mapping or source-reference JSON is missing.",
            )
            raise SystemExit(1)

        self.db = CardDatabase(
            self.mapping_path,
            self.reference_path,
        )
        self.process = ProcessMemory()

        self.selected_card: Optional[str] = None
        self.selected_row: Optional[int] = None
        self.selected_rva: Optional[int] = None

        self.session_originals: dict[int, bytes] = {}
        self.runtime_copy_originals: dict[int, bytes] = {}
        self.record_clipboard: Optional[bytes] = None
        self.last_followed_card: Optional[str] = None

        # Clone/replace source is independent from the currently selected target.
        self.clone_source: Optional[tuple[str, int, int]] = None
        self.session_original_producer_branch: Optional[bytes] = None

        self.search_var = tk.StringVar(value="")
        self.category_var = tk.StringVar(value="All")
        self.follow_selected_var = tk.BooleanVar(value=True)
        self.auto_refresh_var = tk.BooleanVar(value=True)
        self.show_source_only_var = tk.BooleanVar(value=False)
        self.clone_mode_var = tk.StringVar(value=CLONE_MODES[0])
        self.auto_training_compat_var = tk.BooleanVar(value=True)
        self.patch_live_copies_var = tk.BooleanVar(value=True)
        self.show_replace_report_var = tk.BooleanVar(value=True)

        self.module_var = tk.StringVar(value="Not attached.")
        self.selected_object_var = tk.StringVar(value="No object selected.")
        self.card_heading_var = tk.StringVar(value="Select a card.")
        self.record_heading_var = tk.StringVar(value="Select a mapped button.")
        self.alias_var = tk.StringVar(value="")
        self.raw_hex_var = tk.StringVar(value="")
        self.clone_source_var = tk.StringVar(value="No clone source selected.")
        self.compatibility_var = tk.StringVar(
            value="Training compatibility: attach to read the confirmed gate."
        )
        self.producer_map_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(
            value=(
                f"Loaded {len(self.db.effective)} cards and "
                f"{len(self.db.all_unique_rvas())} unique runtime records."
            )
        )

        self.field_vars = {
            name: tk.StringVar(value="")
            for name, *_rest in FIELD_LAYOUT
        }

        self._configure_dark_theme()
        self._build_ui()
        self.populate_card_tree()

        self.after(350, self.poll_selected_object)
        self.after(1000, self.auto_refresh_tick)
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------
    # Theme and UI
    # ------------------------------------------------------------------

    def _configure_dark_theme(self) -> None:
        self.configure(bg="#1E1E1E")

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background="#262626",
            foreground="#E8E8E8",
            fieldbackground="#303030",
            bordercolor="#555555",
            darkcolor="#202020",
            lightcolor="#444444",
        )
        style.configure(
            "TFrame",
            background="#1E1E1E",
        )
        style.configure(
            "TLabelframe",
            background="#1E1E1E",
            foreground="#FFFFFF",
        )
        style.configure(
            "TLabelframe.Label",
            background="#1E1E1E",
            foreground="#FFFFFF",
        )
        style.configure(
            "TLabel",
            background="#1E1E1E",
            foreground="#E8E8E8",
        )
        style.configure(
            "TButton",
            background="#343434",
            foreground="#FFFFFF",
            padding=5,
        )
        style.map(
            "TButton",
            background=[("active", "#484848")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#2D2D2D",
            foreground="#FFFFFF",
            insertcolor="#FFFFFF",
        )
        style.configure(
            "TCombobox",
            fieldbackground="#2D2D2D",
            foreground="#FFFFFF",
        )
        style.configure(
            "Treeview",
            background="#252525",
            fieldbackground="#252525",
            foreground="#E8E8E8",
            rowheight=24,
        )
        style.map(
            "Treeview",
            background=[("selected", "#365A7A")],
            foreground=[("selected", "#FFFFFF")],
        )
        style.configure(
            "Treeview.Heading",
            background="#333333",
            foreground="#FFFFFF",
        )

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill=tk.X)

        ttk.Button(
            toolbar,
            text="Attach",
            command=self.attach,
        ).pack(side=tk.LEFT)

        ttk.Button(
            toolbar,
            text="Detach",
            command=self.detach,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            toolbar,
            text="Refresh",
            command=self.refresh_current_view,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            toolbar,
            text="Import Final Mapping",
            command=self.import_mapping,
        ).pack(side=tk.LEFT, padx=(14, 4))

        ttk.Button(
            toolbar,
            text="Export Mapping Copy",
            command=self.export_mapping_copy,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            toolbar,
            text="Save Live Snapshot",
            command=self.save_snapshot,
        ).pack(side=tk.LEFT, padx=(14, 4))

        ttk.Button(
            toolbar,
            text="Load + Apply Snapshot",
            command=self.load_apply_snapshot,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            toolbar,
            text="Generate Cheat Engine Table",
            command=self.generate_cheat_engine_table,
        ).pack(side=tk.LEFT, padx=4)

        options_toolbar = ttk.Frame(self, padding=(8, 0, 8, 6))
        options_toolbar.pack(fill=tk.X)

        ttk.Button(
            options_toolbar,
            text="Clone / Replace…",
            command=self.show_clone_tab,
        ).pack(side=tk.LEFT)

        ttk.Button(
            options_toolbar,
            text="Enable Unit Cross-Training",
            command=self.enable_training_compatibility,
        ).pack(side=tk.LEFT, padx=(12, 4))

        ttk.Button(
            options_toolbar,
            text="Restore Strict Producer Check",
            command=self.restore_strict_producer_validation,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Checkbutton(
            options_toolbar,
            text="Follow selected object",
            variable=self.follow_selected_var,
        ).pack(side=tk.LEFT, padx=(14, 4))

        ttk.Checkbutton(
            options_toolbar,
            text="Auto refresh",
            variable=self.auto_refresh_var,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Label(
            self,
            textvariable=self.module_var,
            font=("Consolas", 9),
        ).pack(fill=tk.X, padx=8)

        ttk.Label(
            self,
            textvariable=self.selected_object_var,
            font=("Consolas", 10, "bold"),
        ).pack(fill=tk.X, padx=8, pady=(2, 0))

        ttk.Label(
            self,
            textvariable=self.status_var,
        ).pack(fill=tk.X, padx=8, pady=(2, 2))

        compatibility_strip = ttk.Frame(self, padding=(8, 0, 8, 6))
        compatibility_strip.pack(fill=tk.X)
        ttk.Label(
            compatibility_strip,
            textvariable=self.compatibility_var,
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Label(
            compatibility_strip,
            textvariable=self.producer_map_var,
            font=("Consolas", 9),
        ).pack(side=tk.RIGHT)

        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(main, padding=4)
        middle = ttk.Frame(main, padding=4)
        right = ttk.Frame(main, padding=4)

        main.add(left, weight=3)
        main.add(middle, weight=4)
        main.add(right, weight=5)

        # Left panel: cards.
        filter_box = ttk.LabelFrame(
            left,
            text="Cards",
            padding=8,
        )
        filter_box.pack(fill=tk.X)

        ttk.Label(filter_box, text="Search").grid(
            row=0,
            column=0,
            sticky="w",
        )
        search_entry = ttk.Entry(
            filter_box,
            textvariable=self.search_var,
        )
        search_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(6, 0),
        )
        search_entry.bind(
            "<KeyRelease>",
            lambda _event: self.populate_card_tree(),
        )

        ttk.Label(filter_box, text="Category").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        category_combo = ttk.Combobox(
            filter_box,
            textvariable=self.category_var,
            values=[
                "All",
                "Units",
                "Buildings",
                "Build Menus",
                "Generic / Group",
                "Cancel / Special",
                "Human",
                "Orc",
                "Shared",
            ],
            state="readonly",
        )
        category_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(6, 0),
            pady=(6, 0),
        )
        category_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.populate_card_tree(),
        )

        ttk.Checkbutton(
            filter_box,
            text="Show source-only unmapped cards",
            variable=self.show_source_only_var,
            command=self.populate_card_tree,
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )

        filter_box.columnconfigure(1, weight=1)

        cards_box = ttk.LabelFrame(
            left,
            text="Unit / building / menu / cancel cards",
            padding=6,
        )
        cards_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.card_tree = ttk.Treeview(
            cards_box,
            columns=("name", "buttons", "base", "ids"),
            show="headings",
            selectmode="browse",
        )

        for key, title, width in (
            ("name", "Card", 245),
            ("buttons", "Buttons", 65),
            ("base", "Base RVA", 100),
            ("ids", "Card IDs", 90),
        ):
            self.card_tree.heading(key, text=title)
            self.card_tree.column(
                key,
                width=width,
                anchor="w" if key == "name" else "center",
            )

        self.card_tree.pack(fill=tk.BOTH, expand=True)
        self.card_tree.bind(
            "<<TreeviewSelect>>",
            self.on_card_selected,
        )

        # Middle panel: card buttons.
        card_header = ttk.LabelFrame(
            middle,
            text="Selected command card",
            padding=8,
        )
        card_header.pack(fill=tk.X)

        ttk.Label(
            card_header,
            textvariable=self.card_heading_var,
            font=("Segoe UI", 11, "bold"),
            wraplength=700,
        ).pack(anchor="w")

        buttons_box = ttk.LabelFrame(
            middle,
            text="Buttons / records",
            padding=6,
        )
        buttons_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.button_tree = ttk.Treeview(
            buttons_box,
            columns=(
                "button",
                "slot",
                "source_icon",
                "action",
                "record",
                "live_icon",
                "live_tip",
                "mapping",
            ),
            show="headings",
            selectmode="extended",
        )

        for key, title, width in (
            ("button", "#", 38),
            ("slot", "Slot", 42),
            ("source_icon", "Source Icon", 145),
            ("action", "Source Action", 150),
            ("record", "Record RVA", 100),
            ("live_icon", "Live Icon", 70),
            ("live_tip", "Live Tooltip", 82),
            ("mapping", "Mapping", 130),
        ):
            self.button_tree.heading(key, text=title)
            self.button_tree.column(
                key,
                width=width,
                anchor="center"
                if key in {
                    "button", "slot", "record", "live_icon",
                    "live_tip", "mapping",
                }
                else "w",
            )

        self.button_tree.pack(fill=tk.BOTH, expand=True)
        self.button_tree.bind(
            "<<TreeviewSelect>>",
            self.on_button_selected,
        )
        self.button_tree.bind(
            "<Double-1>",
            lambda _event: self.read_selected_record(),
        )
        self.button_tree.bind("<Button-3>", self.show_button_context_menu)

        self.button_context_menu = tk.Menu(
            self,
            tearoff=False,
            bg="#2B2B2B",
            fg="#FFFFFF",
            activebackground="#365A7A",
            activeforeground="#FFFFFF",
        )
        self.button_context_menu.add_command(
            label="Use Selected Button as Clone Source",
            command=self.set_clone_source_from_current,
        )
        self.button_context_menu.add_command(
            label="Replace Selected Target(s) with Clone Source",
            command=self.replace_selected_targets,
        )
        self.button_context_menu.add_separator()
        self.button_context_menu.add_command(
            label="Choose Source from Entire Library…",
            command=self.choose_clone_source,
        )

        # Right panel: split the dense editor and the clone workflow into tabs
        # so every control stays readable without vertical clipping.
        editor_notebook = ttk.Notebook(right)
        editor_notebook.pack(fill=tk.BOTH, expand=True)

        record_tab = ttk.Frame(editor_notebook, padding=6)
        clone_tab = ttk.Frame(editor_notebook, padding=6)
        editor_notebook.add(record_tab, text="Record Editor")
        editor_notebook.add(clone_tab, text="Clone / Replace")

        self.editor_notebook = editor_notebook
        self.clone_tab = clone_tab

        record_box = ttk.LabelFrame(
            record_tab,
            text="24-byte record editor",
            padding=8,
        )
        record_box.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            record_box,
            textvariable=self.record_heading_var,
            font=("Segoe UI", 10, "bold"),
            wraplength=320,
        ).pack(anchor="w")

        alias_label = tk.Label(
            record_box,
            textvariable=self.alias_var,
            bg="#1E1E1E",
            fg="#FFB347",
            justify=tk.LEFT,
            wraplength=320,
        )
        alias_label.pack(fill=tk.X, pady=(4, 4))

        raw_frame = ttk.Frame(record_box)
        raw_frame.pack(fill=tk.X, pady=(2, 8))

        ttk.Label(raw_frame, text="Raw bytes").pack(side=tk.LEFT)
        ttk.Entry(
            raw_frame,
            textvariable=self.raw_hex_var,
            state="readonly",
            font=("Consolas", 9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        fields_frame = ttk.Frame(record_box)
        fields_frame.pack(fill=tk.X)

        for row_number, (name, label, offset, fmt) in enumerate(FIELD_LAYOUT):
            ttk.Label(
                fields_frame,
                text=f"+0x{offset:02X} {label}",
            ).grid(
                row=row_number,
                column=0,
                sticky="w",
                pady=2,
            )

            ttk.Entry(
                fields_frame,
                textvariable=self.field_vars[name],
                width=18,
                font=("Consolas", 10),
            ).grid(
                row=row_number,
                column=1,
                sticky="ew",
                padx=6,
                pady=2,
            )

            ttk.Button(
                fields_frame,
                text="Write",
                command=lambda field=name: self.write_one_field(field),
            ).grid(
                row=row_number,
                column=2,
                sticky="ew",
                pady=2,
            )

        fields_frame.columnconfigure(1, weight=1)

        quick_box = ttk.LabelFrame(
            record_box,
            text="Record actions",
            padding=8,
        )
        quick_box.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(
            quick_box,
            text="Read Record",
            command=self.read_selected_record,
        ).grid(row=0, column=0, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Write Icon Only",
            command=lambda: self.write_one_field("icon_id"),
        ).grid(row=0, column=1, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Write Whole Record",
            command=self.write_whole_record,
        ).grid(row=0, column=2, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Copy Record",
            command=self.copy_record,
        ).grid(row=1, column=0, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Paste Record",
            command=self.paste_record,
        ).grid(row=1, column=1, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Restore This Record",
            command=self.restore_selected_record,
        ).grid(row=1, column=2, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Restore Selected Card",
            command=self.restore_selected_card,
        ).grid(row=2, column=0, sticky="ew", padx=2, pady=2)

        ttk.Button(
            quick_box,
            text="Restore Everything",
            command=self.restore_all_records,
        ).grid(row=2, column=1, columnspan=2, sticky="ew", padx=2, pady=2)

        for column in range(3):
            quick_box.columnconfigure(column, weight=1)

        clone_box = ttk.LabelFrame(
            clone_tab,
            text="Quick clone / replace",
            padding=8,
        )
        clone_box.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(
            clone_box,
            textvariable=self.clone_source_var,
            font=("Segoe UI", 9, "bold"),
            wraplength=320,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Button(
            clone_box,
            text="Current → Source",
            command=self.set_clone_source_from_current,
        ).grid(row=1, column=0, sticky="ew", padx=2, pady=2)

        ttk.Button(
            clone_box,
            text="Browse…",
            command=self.choose_clone_source,
        ).grid(row=1, column=1, sticky="ew", padx=2, pady=2)

        ttk.Button(
            clone_box,
            text="Clear",
            command=self.clear_clone_source,
        ).grid(row=1, column=2, sticky="ew", padx=2, pady=2)

        ttk.Label(clone_box, text="Mode").grid(
            row=2,
            column=0,
            sticky="w",
            padx=2,
            pady=(8, 2),
        )
        clone_mode = ttk.Combobox(
            clone_box,
            textvariable=self.clone_mode_var,
            values=CLONE_MODES,
            state="readonly",
        )
        clone_mode.grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=2,
            pady=(8, 2),
        )

        ttk.Checkbutton(
            clone_box,
            text="Auto training compatibility",
            variable=self.auto_training_compat_var,
        ).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            padx=2,
            pady=(6, 2),
        )

        ttk.Checkbutton(
            clone_box,
            text="Patch mapped record + duplicate/live cached copies",
            variable=self.patch_live_copies_var,
        ).grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="w",
            padx=2,
            pady=(2, 2),
        )

        ttk.Checkbutton(
            clone_box,
            text="Show detailed replacement report",
            variable=self.show_replace_report_var,
        ).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
            padx=2,
            pady=(2, 2),
        )

        ttk.Button(
            clone_box,
            text="REPLACE TARGET(S)",
            command=self.replace_selected_targets,
        ).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=2,
            pady=(8, 2),
        )

        ttk.Button(
            clone_box,
            text="DIRECT TEST: FOOTMAN → GRUNT",
            command=self.direct_footman_to_grunt_test,
        ).grid(
            row=7,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=2,
            pady=(4, 2),
        )

        ttk.Label(
            clone_box,
            text=(
                "Default mode keeps each target's panel slot while copying the source button's icon, "
                "visibility, action, parameters, tooltip, and masks. Live-copy mode also patches exact "
                "cached copies so an already-open command card cannot hide the change."
            ),
            justify=tk.LEFT,
            wraplength=320,
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=2, pady=(6, 0))

        for column in range(3):
            clone_box.columnconfigure(column, weight=1)

        safety_box = ttk.LabelFrame(
            clone_tab,
            text="Notes",
            padding=8,
        )
        safety_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=(8, 4))

        ttk.Label(
            safety_box,
            text=(
                "Values accept 0x-prefixed hexadecimal or plain decimal.\n\n"
                "Icon ID is the safest field to experiment with. Callback "
                "pointers and masks can crash or radically change game behavior.\n\n"
                "Original bytes are captured when you attach. Restore actions "
                "return records to that attach-time state.\n\n"
                "Shared-address warnings are shown above. Editing a shared "
                "record changes every card/button that aliases the same RVA.\n\n"
                "Quick Clone defaults to preserving the target slot, so copied "
                "buttons stay in the selected target position."
            ),
            justify=tk.LEFT,
            wraplength=320,
        ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Card database and selection
    # ------------------------------------------------------------------

    def card_matches_filter(self, array_name: str) -> bool:
        search = self.search_var.get().strip().lower()
        if search:
            haystack = (
                f"{array_name} {self.db.friendly(array_name)} "
                f"{self.db.category(array_name)} {self.db.faction(array_name)}"
            ).lower()
            if search not in haystack:
                return False

        selected = self.category_var.get()
        if selected == "All":
            return True
        if selected in {"Human", "Orc", "Shared"}:
            return self.db.faction(array_name) == selected
        return self.db.category(array_name) == selected

    def populate_card_tree(self) -> None:
        current = self.selected_card
        self.card_tree.delete(*self.card_tree.get_children())

        names = set(self.db.effective)
        if self.show_source_only_var.get():
            names.update(self.db.reference.get("arrays", {}))

        ordered = sorted(
            names,
            key=lambda name: (
                self.db.category(name),
                self.db.faction(name),
                self.db.friendly(name),
            ),
        )

        for array_name in ordered:
            if not self.card_matches_filter(array_name):
                continue

            effective = self.db.effective.get(array_name, {})
            source_only = not effective

            base = None
            if effective:
                base = min(effective.values())

            card_ids = self.db.card_ids(array_name)
            card_id_text = ",".join(f"{value:02X}" for value in card_ids)

            self.card_tree.insert(
                "",
                "end",
                iid=array_name,
                values=(
                    (
                        f"{self.db.friendly(array_name)} "
                        f"[{array_name}]"
                    ),
                    len(effective),
                    f"+0x{base:08X}" if base is not None else "—",
                    card_id_text or "—",
                ),
                tags=("source_only",) if source_only else (),
            )

        self.card_tree.tag_configure(
            "source_only",
            foreground="#777777",
        )

        if current and self.card_tree.exists(current):
            self.card_tree.selection_set(current)
            self.card_tree.focus(current)
            self.card_tree.see(current)
        elif self.card_tree.get_children():
            first = self.card_tree.get_children()[0]
            self.card_tree.selection_set(first)
            self.card_tree.focus(first)
            self.select_card(first)

    def on_card_selected(self, _event=None) -> None:
        selection = self.card_tree.selection()
        if selection:
            self.select_card(selection[0])

    def select_card(self, array_name: str) -> None:
        self.selected_card = array_name
        self.selected_row = None
        self.selected_rva = None

        effective = self.db.effective.get(array_name, {})
        card_ids = self.db.card_ids(array_name)

        self.card_heading_var.set(
            f"{self.db.friendly(array_name)} [{array_name}] | "
            f"{self.db.category(array_name)} | {self.db.faction(array_name)} | "
            f"{len(effective)} editable buttons | "
            f"Card IDs: "
            f"{', '.join(f'0x{value:02X}' for value in card_ids) or 'none'}"
        )

        self.populate_button_tree()
        self.clear_editor()

        if effective:
            first_row = sorted(effective)[0]
            iid = str(first_row)
            if self.button_tree.exists(iid):
                self.button_tree.selection_set(iid)
                self.button_tree.focus(iid)
                self.button_tree.see(iid)
                self.select_button(first_row)

    def populate_button_tree(self) -> None:
        previous_selection = tuple(self.button_tree.selection())
        previous_focus = self.button_tree.focus()

        self.button_tree.delete(*self.button_tree.get_children())

        if not self.selected_card:
            return

        source_rows = self.db.source_rows(self.selected_card)
        effective = self.db.effective.get(self.selected_card, {})
        skipped = self.db.mapping.get(
            "skipped_buttons",
            {},
        ).get(self.selected_card, {})

        for row_index, source_row in enumerate(source_rows):
            if str(row_index) in skipped or self.db.is_legacy_row(source_row):
                self.button_tree.insert(
                    "",
                    "end",
                    iid=str(row_index),
                    values=(
                        row_index + 1,
                        source_row[0],
                        source_row[1],
                        source_row[3],
                        "—",
                        "—",
                        "—",
                        "classic-only / skipped",
                    ),
                    tags=("skipped",),
                )
                continue

            rva = effective.get(row_index)
            if rva is None:
                self.button_tree.insert(
                    "",
                    "end",
                    iid=str(row_index),
                    values=(
                        row_index + 1,
                        source_row[0],
                        source_row[1],
                        source_row[3],
                        "—",
                        "—",
                        "—",
                        "Unmapped",
                    ),
                    tags=("unmapped",),
                )
                continue

            live_icon = "—"
            live_tip = "—"
            if self.process.handle:
                raw = self.read_record(rva)
                if raw:
                    live_icon = f"0x{struct.unpack_from('<H', raw, 0x02)[0]:04X}"
                    live_tip = f"0x{struct.unpack_from('<H', raw, 0x12)[0]:04X}"

            mapping_source = self.db.row_sources.get(
                (self.selected_card, row_index),
                "Mapped",
            )

            alias_count = len(self.db.reverse.get(rva, []))
            if alias_count > 1:
                mapping_source += f" | {alias_count} aliases"

            self.button_tree.insert(
                "",
                "end",
                iid=str(row_index),
                values=(
                    row_index + 1,
                    source_row[0],
                    source_row[1],
                    source_row[3],
                    f"+0x{rva:08X}",
                    live_icon,
                    live_tip,
                    mapping_source,
                ),
                tags=("shared",) if alias_count > 1 else (),
            )

        self.button_tree.tag_configure(
            "skipped",
            foreground="#777777",
        )
        self.button_tree.tag_configure(
            "unmapped",
            foreground="#A85D5D",
        )
        self.button_tree.tag_configure(
            "shared",
            foreground="#FFB347",
        )

        surviving = [
            iid for iid in previous_selection
            if self.button_tree.exists(iid)
        ]
        if surviving:
            self.button_tree.selection_set(surviving)
            focus = previous_focus if previous_focus in surviving else surviving[-1]
            self.button_tree.focus(focus)
            self.button_tree.see(focus)

    def on_button_selected(self, _event=None) -> None:
        selection = self.button_tree.selection()
        if not selection:
            return

        focused = self.button_tree.focus()
        selected_iid = focused if focused in selection else selection[-1]
        self.select_button(int(selected_iid))

    def select_button(self, row_index: int) -> None:
        if not self.selected_card:
            return

        rva = self.db.effective.get(
            self.selected_card,
            {},
        ).get(row_index)

        self.selected_row = row_index
        self.selected_rva = rva

        if rva is None:
            self.clear_editor()
            self.record_heading_var.set(
                f"Button {row_index + 1} is skipped or has no confirmed runtime record."
            )
            return

        aliases = self.db.aliases(rva)
        self.record_heading_var.set(
            f"{self.db.friendly(self.selected_card)} | "
            f"Button {row_index + 1} | "
            f"Record Warcraft II.exe+{rva:08X}"
        )

        if len(aliases) > 1:
            self.alias_var.set(
                "SHARED ADDRESS — editing this record affects:\n"
                + "\n".join(f"• {alias}" for alias in aliases)
            )
        else:
            self.alias_var.set("")

        self.read_selected_record()

    # ------------------------------------------------------------------
    # Record reading and writing
    # ------------------------------------------------------------------

    def parse_number(self, text: str) -> int:
        value = text.strip().replace("_", "")
        if not value:
            raise ValueError("A value is required.")

        if value.lower().startswith("0x"):
            return int(value, 16)

        if re.fullmatch(r"[0-9]+", value):
            return int(value, 10)

        if re.fullmatch(r"[0-9A-Fa-f]+", value):
            return int(value, 16)

        return int(value, 0)

    def read_record(self, rva: int) -> bytes:
        if not self.process.handle:
            return b""
        return self.process.read(
            self.process.base + rva,
            RECORD_SIZE,
        )

    def decode_record(self, raw: bytes) -> dict[str, int]:
        if len(raw) != RECORD_SIZE:
            raise ValueError("A command-card record must be 24 bytes.")

        result = {}
        for name, _label, offset, fmt in FIELD_LAYOUT:
            result[name] = struct.unpack_from(
                "<" + fmt,
                raw,
                offset,
            )[0]
        return result

    def encode_editor_record(self) -> bytes:
        raw = bytearray(RECORD_SIZE)

        for name, _label, offset, fmt in FIELD_LAYOUT:
            info = FIELD_INFO[name]
            value = self.parse_number(
                self.field_vars[name].get()
            )

            maximum = (1 << (info["size"] * 8)) - 1
            if not 0 <= value <= maximum:
                raise ValueError(
                    f"{info['label']} must be from 0 through 0x{maximum:X}."
                )

            struct.pack_into("<" + fmt, raw, offset, value)

        return bytes(raw)

    def fill_editor(self, raw: bytes) -> None:
        values = self.decode_record(raw)

        for name, value in values.items():
            size = FIELD_INFO[name]["size"]
            width = size * 2
            self.field_vars[name].set(
                f"0x{value:0{width}X}"
            )

        self.raw_hex_var.set(
            " ".join(f"{byte:02X}" for byte in raw)
        )

    def clear_editor(self) -> None:
        for variable in self.field_vars.values():
            variable.set("")
        self.raw_hex_var.set("")
        self.alias_var.set("")

    def require_selected_record(self) -> int:
        if self.selected_rva is None:
            raise RuntimeError("Select an editable button first.")
        if not self.process.handle:
            raise RuntimeError("Attach to Warcraft II.exe first.")
        return self.selected_rva

    def read_selected_record(self) -> None:
        try:
            rva = self.require_selected_record()
            raw = self.read_record(rva)

            if len(raw) != RECORD_SIZE:
                raise RuntimeError(
                    f"Could not read Warcraft II.exe+{rva:08X}."
                )

            self.fill_editor(raw)
            self.status_var.set(
                f"Read record +0x{rva:08X}."
            )
        except Exception as exc:
            if self.process.handle or self.selected_rva is not None:
                self.status_var.set(str(exc))

    def shared_write_warning(self, rva: int) -> bool:
        aliases = self.db.aliases(rva)
        if len(aliases) <= 1:
            return True

        return messagebox.askyesno(
            APP_TITLE,
            (
                "This runtime record is shared by multiple buttons:\n\n"
                + "\n".join(aliases)
                + "\n\nWrite it anyway?"
            ),
        )

    def write_bytes(self, rva: int, raw: bytes) -> None:
        if len(raw) != RECORD_SIZE:
            raise ValueError("A full record must be exactly 24 bytes.")

        if not self.shared_write_warning(rva):
            raise RuntimeError("Write cancelled.")

        if not self.process.write_protected(
            self.process.base + rva,
            raw,
        ):
            raise RuntimeError(
                f"Could not write record +0x{rva:08X}."
            )

        verify = self.read_record(rva)
        if verify != raw:
            raise RuntimeError(
                f"Record +0x{rva:08X} did not verify after writing."
            )

    def write_one_field(self, field_name: str) -> None:
        try:
            rva = self.require_selected_record()

            if not self.shared_write_warning(rva):
                return

            info = FIELD_INFO[field_name]
            value = self.parse_number(
                self.field_vars[field_name].get()
            )
            maximum = (1 << (info["size"] * 8)) - 1

            if not 0 <= value <= maximum:
                raise ValueError(
                    f"{info['label']} must be from 0 through 0x{maximum:X}."
                )

            raw = struct.pack(
                "<" + info["fmt"],
                value,
            )
            address = (
                self.process.base
                + rva
                + info["offset"]
            )

            if not self.process.write_protected(address, raw):
                raise RuntimeError(
                    f"Could not write {info['label']}."
                )

            if self.process.read(address, len(raw)) != raw:
                raise RuntimeError(
                    f"{info['label']} did not verify after writing."
                )

            self.read_selected_record()
            self.populate_button_tree()
            self.status_var.set(
                f"Wrote {info['label']} at "
                f"Warcraft II.exe+{rva + info['offset']:08X}."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def write_whole_record(self) -> None:
        try:
            rva = self.require_selected_record()

            if not messagebox.askyesno(
                APP_TITLE,
                (
                    "Write every field in this 24-byte record?\n\n"
                    "Callback pointers and masks are included."
                ),
            ):
                return

            raw = self.encode_editor_record()
            self.write_bytes(rva, raw)
            self.read_selected_record()
            self.populate_button_tree()
            self.status_var.set(
                f"Wrote complete record +0x{rva:08X}."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def copy_record(self) -> None:
        try:
            rva = self.require_selected_record()
            raw = self.read_record(rva)
            if len(raw) != RECORD_SIZE:
                raise RuntimeError("Could not read the selected record.")

            self.record_clipboard = raw
            self.status_var.set(
                f"Copied record +0x{rva:08X}."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def paste_record(self) -> None:
        try:
            rva = self.require_selected_record()
            if self.record_clipboard is None:
                raise RuntimeError("Copy a record first.")

            if not messagebox.askyesno(
                APP_TITLE,
                (
                    "Paste all 24 copied bytes over the selected record?\n\n"
                    "This includes callback pointers and masks."
                ),
            ):
                return

            self.write_bytes(rva, self.record_clipboard)
            self.read_selected_record()
            self.populate_button_tree()
            self.status_var.set(
                f"Pasted record at +0x{rva:08X}."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    # ------------------------------------------------------------------
    # Quick clone / replace and confirmed training compatibility
    # ------------------------------------------------------------------

    def show_button_context_menu(self, event) -> None:
        row = self.button_tree.identify_row(event.y)
        if row:
            if row not in self.button_tree.selection():
                self.button_tree.selection_set(row)
            self.button_tree.focus(row)
            self.select_button(int(row))

        try:
            self.button_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.button_context_menu.grab_release()

    def describe_button_choice(
        self,
        choice: tuple[str, int, int],
    ) -> str:
        array_name, row_index, rva = choice
        source_rows = self.db.source_rows(array_name)
        source = source_rows[row_index] if row_index < len(source_rows) else []
        slot = source[0] if len(source) > 0 else "?"
        icon = source[1] if len(source) > 1 else "?"
        action = source[3] if len(source) > 3 else "?"
        parameter = source[5] if len(source) > 5 else "?"
        return (
            f"Source: {self.db.friendly(array_name)} | Button {row_index + 1} | "
            f"Slot {slot} | {icon} | {action} | {parameter} | +0x{rva:08X}"
        )

    def set_clone_source(
        self,
        choice: tuple[str, int, int],
    ) -> None:
        self.clone_source = choice
        self.clone_source_var.set(self.describe_button_choice(choice))
        if hasattr(self, "editor_notebook") and hasattr(self, "clone_tab"):
            self.editor_notebook.select(self.clone_tab)
        self.status_var.set("Clone source selected. Choose one or more target buttons and replace them.")

    def show_clone_tab(self) -> None:
        if hasattr(self, "editor_notebook") and hasattr(self, "clone_tab"):
            self.editor_notebook.select(self.clone_tab)

    def set_clone_source_from_current(self) -> None:
        try:
            if self.selected_card is None or self.selected_row is None or self.selected_rva is None:
                raise RuntimeError("Select a confirmed button first.")
            self.set_clone_source(
                (self.selected_card, self.selected_row, self.selected_rva)
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def choose_clone_source(self) -> None:
        dialog = ButtonLibraryDialog(self)
        self.wait_window(dialog)
        if dialog.result is not None:
            self.set_clone_source(dialog.result)

    def clear_clone_source(self) -> None:
        self.clone_source = None
        self.clone_source_var.set("No clone source selected.")
        self.status_var.set("Clone source cleared.")

    def selected_target_records(self) -> list[tuple[str, int, int]]:
        if not self.selected_card:
            return []

        result = []
        for iid in self.button_tree.selection():
            row_index = int(iid)
            rva = self.db.effective.get(self.selected_card, {}).get(row_index)
            if rva is not None:
                result.append((self.selected_card, row_index, rva))

        if not result and self.selected_rva is not None and self.selected_row is not None:
            result.append((self.selected_card, self.selected_row, self.selected_rva))

        return result

    def build_replacement_record(
        self,
        source_raw: bytes,
        target_raw: bytes,
        mode: str,
    ) -> bytes:
        if len(source_raw) != RECORD_SIZE or len(target_raw) != RECORD_SIZE:
            raise ValueError("Both source and target must be complete 24-byte records.")

        result = bytearray(target_raw)

        if mode == CLONE_MODES[0]:
            # Preserve the target's command-card panel slot; copy all payload fields.
            result[0x02:0x18] = source_raw[0x02:0x18]
        elif mode == CLONE_MODES[1]:
            result[:] = source_raw
        elif mode == CLONE_MODES[2]:
            result[0x02:0x04] = source_raw[0x02:0x04]  # icon
            result[0x12:0x14] = source_raw[0x12:0x14]  # tooltip
        elif mode == CLONE_MODES[3]:
            result[0x04:0x12] = source_raw[0x04:0x12]
            result[0x14:0x18] = source_raw[0x14:0x18]
        else:
            raise ValueError(f"Unknown replacement mode: {mode}")

        return bytes(result)

    def patch_target_and_live_copies(
        self,
        target_rva: int,
        source_raw: bytes,
        mode: str,
    ) -> tuple[int, list[int], bytes, bytes]:
        target_address = self.process.base + target_rva
        target_before = self.read_record(target_rva)

        if len(target_before) != RECORD_SIZE:
            raise RuntimeError(
                f"Could not read target record +0x{target_rva:08X}."
            )

        replacement = self.build_replacement_record(
            source_raw,
            target_before,
            mode,
        )

        addresses = [target_address]
        if self.patch_live_copies_var.get():
            addresses = self.process.find_exact_copies(
                target_before,
                target_alignment=target_address,
                include_live_private=True,
            )
            if target_address not in addresses:
                addresses.insert(0, target_address)

        written_addresses: list[int] = []

        for address in addresses:
            current = self.process.read(address, RECORD_SIZE)
            if current != target_before:
                continue

            self.runtime_copy_originals.setdefault(address, current)

            if not self.process.write_protected(address, replacement):
                continue

            if self.process.read(address, RECORD_SIZE) == replacement:
                written_addresses.append(address)

        mapped_after = self.read_record(target_rva)
        if mapped_after != replacement:
            raise RuntimeError(
                f"Mapped target +0x{target_rva:08X} did not verify after writing."
            )

        return (
            len(written_addresses),
            written_addresses,
            target_before,
            replacement,
        )

    def direct_footman_to_grunt_test(self) -> None:
        """Direct known-address test that bypasses source/target UI selection."""
        try:
            if not self.process.handle:
                raise RuntimeError("Attach to Warcraft II.exe first.")

            source_rva = 0x004C87F0
            target_rva = 0x004C8760

            source_raw = self.read_record(source_rva)
            if len(source_raw) != RECORD_SIZE:
                raise RuntimeError("Could not read the confirmed Grunt record.")

            count, addresses, before, after = self.patch_target_and_live_copies(
                target_rva,
                source_raw,
                CLONE_MODES[0],
            )

            compatibility_enabled = self.enable_training_compatibility(
                quiet=True,
            )

            self.refresh_current_view()

            old_icon = struct.unpack_from("<H", before, 0x02)[0]
            new_icon = struct.unpack_from("<H", after, 0x02)[0]
            address_text = ", ".join(
                f"0x{address:08X}" for address in addresses[:10]
            )
            if len(addresses) > 10:
                address_text += ", ..."

            report = (
                "DIRECT FOOTMAN → GRUNT TEST COMPLETED\n\n"
                f"Source: Warcraft II.exe+{source_rva:08X}\n"
                f"Target: Warcraft II.exe+{target_rva:08X}\n"
                f"Icon: 0x{old_icon:04X} → 0x{new_icon:04X}\n"
                f"Verified copies patched: {count}\n"
                f"Addresses: {address_text or 'none'}\n"
                f"Training compatibility: "
                f"{'enabled' if compatibility_enabled else 'not enabled'}\n\n"
                "Select another object in Warcraft II, then reselect the Human Barracks."
            )

            self.status_var.set(
                f"Direct Footman→Grunt test patched {count} verified copy/copies."
            )
            messagebox.showinfo(APP_TITLE, report)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def replace_selected_targets(self) -> None:
        try:
            if not self.process.handle:
                raise RuntimeError("Attach to Warcraft II.exe first.")
            if self.clone_source is None:
                raise RuntimeError("Choose a clone source first.")

            targets = self.selected_target_records()
            if not targets:
                raise RuntimeError("Select one or more confirmed target buttons.")

            source_array, source_row, source_rva = self.clone_source
            source_raw = self.read_record(source_rva)
            if len(source_raw) != RECORD_SIZE:
                raise RuntimeError("Could not read the live source record.")

            # Deduplicate shared target RVAs while retaining a useful description.
            unique_targets: dict[int, tuple[str, int, int]] = {}
            for target in targets:
                unique_targets.setdefault(target[2], target)

            mode = self.clone_mode_var.get()
            source_description = self.describe_button_choice(self.clone_source)
            target_lines = []
            for array_name, row_index, rva in unique_targets.values():
                target_lines.append(
                    f"• {self.db.friendly(array_name)} Button {row_index + 1} (+0x{rva:08X})"
                )

            if not messagebox.askyesno(
                APP_TITLE,
                (
                    f"{source_description}\n\n"
                    f"Mode: {mode}\n\n"
                    f"Replace {len(unique_targets)} target record(s):\n"
                    + "\n".join(target_lines)
                    + "\n\nProceed?"
                ),
            ):
                return

            written = 0
            patched_copy_count = 0
            failures = []
            replacement_reports = []

            for rva, (_array_name, _row_index, _target_rva) in unique_targets.items():
                try:
                    (
                        copy_count,
                        patched_addresses,
                        target_before,
                        replacement,
                    ) = self.patch_target_and_live_copies(
                        rva,
                        source_raw,
                        mode,
                    )

                    written += 1
                    patched_copy_count += copy_count

                    old_icon = struct.unpack_from("<H", target_before, 0x02)[0]
                    new_icon = struct.unpack_from("<H", replacement, 0x02)[0]
                    old_action = struct.unpack_from("<I", target_before, 0x0C)[0]
                    new_action = struct.unpack_from("<I", replacement, 0x0C)[0]

                    address_preview = ", ".join(
                        f"0x{address:08X}"
                        for address in patched_addresses[:8]
                    )
                    if len(patched_addresses) > 8:
                        address_preview += ", ..."

                    replacement_reports.append(
                        (
                            f"Target +0x{rva:08X}\n"
                            f"  Icon: 0x{old_icon:04X} → 0x{new_icon:04X}\n"
                            f"  Action: 0x{old_action:08X} → 0x{new_action:08X}\n"
                            f"  Verified copies patched: {copy_count}\n"
                            f"  Addresses: {address_preview or 'none'}"
                        )
                    )
                except Exception as exc:
                    failures.append(f"+0x{rva:08X}: {exc}")

            source_rows = self.db.source_rows(source_array)
            source_metadata = source_rows[source_row]
            source_action = source_metadata[3] if len(source_metadata) > 3 else ""

            compatibility_enabled = False
            if (
                written
                and self.auto_training_compat_var.get()
                and source_action == "bldg_build_man"
                and mode in {CLONE_MODES[0], CLONE_MODES[1], CLONE_MODES[3]}
            ):
                compatibility_enabled = self.enable_training_compatibility(
                    quiet=True,
                )

            self.refresh_current_view()
            self.refresh_compatibility_status()

            message = (
                f"Replaced {written}/{len(unique_targets)} mapped target record(s); "
                f"patched {patched_copy_count} verified mapped/live copy/copies."
            )
            if compatibility_enabled:
                message += " Confirmed unit-training compatibility was enabled."
            if failures:
                message += " Failures: " + "; ".join(failures)
            self.status_var.set(message)

            detailed = (
                f"{source_description}\n\n"
                f"Mode: {mode}\n"
                f"Mapped targets replaced: {written}/{len(unique_targets)}\n"
                f"Total verified mapped/live copies patched: {patched_copy_count}\n"
                f"Training compatibility: "
                f"{'enabled' if compatibility_enabled else 'unchanged'}\n\n"
                + "\n\n".join(replacement_reports)
                + "\n\nSelect another object in Warcraft II, then reselect the edited building "
                  "to force the command card to redraw."
            )

            if failures:
                detailed += "\n\nFAILURES\n" + "\n".join(failures)
                messagebox.showwarning(APP_TITLE, detailed)
            elif self.show_replace_report_var.get():
                messagebox.showinfo(APP_TITLE, detailed)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def producer_branch_address(self) -> int:
        if not self.process.handle:
            raise RuntimeError("Attach to Warcraft II.exe first.")
        return self.process.base + PRODUCER_MISMATCH_BRANCH_RVA

    def read_producer_branch(self) -> bytes:
        if not self.process.handle:
            return b""
        return self.process.read(
            self.process.base + PRODUCER_MISMATCH_BRANCH_RVA,
            2,
        )

    def training_compatibility_enabled(self) -> bool:
        return self.read_producer_branch() == PRODUCER_MISMATCH_PATCH

    def refresh_compatibility_status(self) -> None:
        if not self.process.handle:
            self.compatibility_var.set(
                "Training compatibility: attach to read the confirmed gate."
            )
            self.producer_map_var.set("")
            return

        branch = self.read_producer_branch()
        table = self.process.base + PRODUCER_TABLE_RVA
        peasant = self.process.read_u8(table + 0x02)
        peon = self.process.read_u8(table + 0x03)

        if branch == PRODUCER_MISMATCH_PATCH:
            self.compatibility_var.set(
                "Unit-training compatibility: ENABLED (producer mismatch branch is NOP NOP)."
            )
        elif branch == PRODUCER_MISMATCH_ORIGINAL:
            self.compatibility_var.set(
                "Unit-training compatibility: strict original producer check is active."
            )
        else:
            self.compatibility_var.set(
                "Unit-training compatibility: unexpected gate bytes "
                + (" ".join(f"{byte:02X}" for byte in branch) if branch else "unreadable")
            )

        if peasant is not None and peon is not None:
            self.producer_map_var.set(
                f"Producer table: Peasant 02→{peasant:02X} | Peon 03→{peon:02X}"
            )
        else:
            self.producer_map_var.set("Producer table unavailable")

    def enable_training_compatibility(self, quiet: bool = False) -> bool:
        try:
            address = self.producer_branch_address()
            current = self.process.read(address, 2)

            if current == PRODUCER_MISMATCH_PATCH:
                self.refresh_compatibility_status()
                if not quiet:
                    self.status_var.set("Unit-training compatibility is already enabled.")
                return True

            if current != PRODUCER_MISMATCH_ORIGINAL:
                raise RuntimeError(
                    "The confirmed producer-mismatch branch does not contain expected bytes "
                    f"75 29 or 90 90. Current: "
                    + (" ".join(f"{byte:02X}" for byte in current) if current else "unreadable")
                )

            if self.session_original_producer_branch is None:
                self.session_original_producer_branch = current

            if not self.process.write_protected(address, PRODUCER_MISMATCH_PATCH):
                raise RuntimeError("Could not enable unit-training compatibility.")
            if self.process.read(address, 2) != PRODUCER_MISMATCH_PATCH:
                raise RuntimeError("Compatibility patch did not verify after writing.")

            self.refresh_compatibility_status()
            if not quiet:
                self.status_var.set(
                    "Enabled unit-training compatibility: Warcraft II.exe+AC873 75 29 → 90 90."
                )
            return True
        except Exception as exc:
            if quiet:
                self.status_var.set(str(exc))
                return False
            messagebox.showerror(APP_TITLE, str(exc))
            return False

    def restore_strict_producer_validation(self, quiet: bool = False) -> bool:
        try:
            address = self.producer_branch_address()
            current = self.process.read(address, 2)

            if current == PRODUCER_MISMATCH_ORIGINAL:
                self.refresh_compatibility_status()
                if not quiet:
                    self.status_var.set("Strict producer validation is already restored.")
                return True

            if current != PRODUCER_MISMATCH_PATCH:
                raise RuntimeError(
                    "The confirmed producer-mismatch branch contains unexpected bytes: "
                    + (" ".join(f"{byte:02X}" for byte in current) if current else "unreadable")
                )

            restore = self.session_original_producer_branch or PRODUCER_MISMATCH_ORIGINAL
            if not self.process.write_protected(address, restore):
                raise RuntimeError("Could not restore strict producer validation.")
            if self.process.read(address, 2) != restore:
                raise RuntimeError("Strict validation bytes did not verify after restoring.")

            self.refresh_compatibility_status()
            if not quiet:
                self.status_var.set(
                    "Restored strict producer validation at Warcraft II.exe+AC873."
                )
            return True
        except Exception as exc:
            if quiet:
                self.status_var.set(str(exc))
                return False
            messagebox.showerror(APP_TITLE, str(exc))
            return False

    # ------------------------------------------------------------------
    # Attach, refresh, restore
    # ------------------------------------------------------------------

    def attach(self) -> None:
        try:
            self.process.attach()

            self.module_var.set(
                f"PID {self.process.pid} | "
                f"base 0x{self.process.base:08X} | "
                f"size 0x{self.process.size:X} | "
                f"{self.process.path}"
            )

            self.capture_session_originals()
            current_branch = self.read_producer_branch()
            if current_branch == PRODUCER_MISMATCH_ORIGINAL:
                self.session_original_producer_branch = current_branch
            self.refresh_current_view()
            self.refresh_compatibility_status()

            self.status_var.set(
                f"Attached and captured "
                f"{len(self.session_originals)} unique original records."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def detach(self) -> None:
        self.process.close()
        self.session_originals = {}
        self.session_original_producer_branch = None
        self.module_var.set("Not attached.")
        self.selected_object_var.set("No object selected.")
        self.populate_button_tree()
        self.clear_editor()
        self.refresh_compatibility_status()
        self.status_var.set("Detached.")

    def capture_session_originals(self) -> None:
        self.session_originals = {}

        for rva in self.db.all_unique_rvas():
            raw = self.read_record(rva)
            if len(raw) == RECORD_SIZE:
                self.session_originals[rva] = raw

    def refresh_current_view(self) -> None:
        self.populate_button_tree()
        if self.selected_rva is not None and self.process.handle:
            self.read_selected_record()
        self.refresh_compatibility_status()

    def auto_refresh_tick(self) -> None:
        try:
            if self.auto_refresh_var.get() and self.process.handle:
                self.populate_button_tree()
                self.refresh_compatibility_status()
        except Exception:
            pass
        finally:
            self.after(1000, self.auto_refresh_tick)

    def restore_selected_record(self) -> None:
        try:
            rva = self.require_selected_record()
            original = self.session_originals.get(rva)

            if original is None:
                raise RuntimeError(
                    "No attach-time original was captured for this record."
                )

            if not self.process.write_protected(
                self.process.base + rva,
                original,
            ):
                raise RuntimeError("Could not restore the record.")

            self.read_selected_record()
            self.populate_button_tree()
            self.status_var.set(
                f"Restored record +0x{rva:08X}."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def restore_selected_card(self) -> None:
        if not self.selected_card or not self.process.handle:
            return

        rvas = sorted(
            set(self.db.effective.get(self.selected_card, {}).values())
        )

        if not messagebox.askyesno(
            APP_TITLE,
            f"Restore all {len(rvas)} records for the selected card?",
        ):
            return

        restored = 0
        for rva in rvas:
            original = self.session_originals.get(rva)
            if original and self.process.write_protected(
                self.process.base + rva,
                original,
            ):
                restored += 1

        self.refresh_current_view()
        self.status_var.set(
            f"Restored {restored}/{len(rvas)} selected-card records."
        )

    def restore_all_records(self) -> None:
        if not self.process.handle:
            return

        if not messagebox.askyesno(
            APP_TITLE,
            (
                f"Restore all {len(self.session_originals)} captured records "
                "and restore the strict producer-validation branch?"
            ),
        ):
            return

        restored = 0
        for rva, original in self.session_originals.items():
            if self.process.write_protected(
                self.process.base + rva,
                original,
            ):
                restored += 1

        runtime_restored = 0
        for address, original in self.runtime_copy_originals.items():
            if self.process.write_protected(address, original):
                if self.process.read(address, len(original)) == original:
                    runtime_restored += 1
        self.runtime_copy_originals.clear()

        strict_restored = self.restore_strict_producer_validation(quiet=True)
        self.refresh_current_view()
        self.status_var.set(
            f"Restored {restored}/{len(self.session_originals)} mapped records, "
            f"{runtime_restored} duplicate/live copies"
            + (" and strict producer validation." if strict_restored else ".")
        )

    # ------------------------------------------------------------------
    # Selected object following
    # ------------------------------------------------------------------

    def poll_selected_object(self) -> None:
        try:
            if self.process.handle:
                pointer = self.process.read_u32(
                    self.process.base
                    + SELECTED_OBJECT_POINTER_RVA
                ) or 0

                if not pointer:
                    self.selected_object_var.set(
                        "No object selected."
                    )
                else:
                    card_id = self.process.read_u8(
                        pointer + SELECTED_CARD_ID_OFFSET
                    )
                    array_name = None

                    if (
                        card_id is not None
                        and card_id
                        < len(self.db.reference.get("cards", []))
                    ):
                        array_name = self.db.reference["cards"][card_id]

                    self.selected_object_var.set(
                        f"Selected object 0x{pointer:08X} | "
                        f"Card "
                        f"{f'0x{card_id:02X}' if card_id is not None else '??'} | "
                        f"{self.db.friendly(array_name) if array_name else 'Unknown'} "
                        f"[{array_name or 'none'}]"
                    )

                    if (
                        self.follow_selected_var.get()
                        and array_name in self.db.effective
                        and array_name != self.last_followed_card
                    ):
                        self.last_followed_card = array_name

                        # Clear filters if they hide the selected card.
                        if not self.card_tree.exists(array_name):
                            self.search_var.set("")
                            self.category_var.set("All")
                            self.populate_card_tree()

                        if self.card_tree.exists(array_name):
                            self.card_tree.selection_set(array_name)
                            self.card_tree.focus(array_name)
                            self.card_tree.see(array_name)
                            self.select_card(array_name)
        except Exception:
            pass
        finally:
            self.after(350, self.poll_selected_object)

    # ------------------------------------------------------------------
    # Snapshot, mapping, and CE export
    # ------------------------------------------------------------------

    def save_snapshot(self) -> None:
        if not self.process.handle:
            messagebox.showwarning(
                APP_TITLE,
                "Attach before saving a live snapshot.",
            )
            return

        filename = filedialog.asksaveasfilename(
            title="Save command-card snapshot",
            defaultextension=".json",
            initialfile="War2_Command_Card_Snapshot.json",
            filetypes=[("JSON", "*.json")],
        )
        if not filename:
            return

        records = {}
        for rva in self.db.all_unique_rvas():
            raw = self.read_record(rva)
            if len(raw) == RECORD_SIZE:
                records[f"{rva:08X}"] = raw.hex().upper()

        payload = {
            "format": "War2RemasterCommandCardSnapshot",
            "record_size": RECORD_SIZE,
            "module_size": self.process.size,
            "unit_training_compatibility": self.training_compatibility_enabled(),
            "producer_mismatch_branch": self.read_producer_branch().hex().upper(),
            "records": records,
        }

        Path(filename).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        self.status_var.set(
            f"Saved {len(records)} records to {filename}."
        )

    def load_apply_snapshot(self) -> None:
        if not self.process.handle:
            messagebox.showwarning(
                APP_TITLE,
                "Attach before applying a snapshot.",
            )
            return

        filename = filedialog.askopenfilename(
            title="Load command-card snapshot",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return

        try:
            payload = json.loads(
                Path(filename).read_text(encoding="utf-8")
            )

            if payload.get("format") != "War2RemasterCommandCardSnapshot":
                raise ValueError("This is not a command-card snapshot.")

            records = payload.get("records", {})

            if not messagebox.askyesno(
                APP_TITLE,
                f"Apply {len(records)} complete 24-byte records?",
            ):
                return

            written = 0
            for rva_text, hex_text in records.items():
                rva = int(rva_text, 16)
                raw = bytes.fromhex(hex_text)

                if len(raw) != RECORD_SIZE:
                    continue

                if self.process.write_protected(
                    self.process.base + rva,
                    raw,
                ):
                    written += 1

            compatibility = payload.get("unit_training_compatibility")
            if compatibility is True:
                self.enable_training_compatibility(quiet=True)
            elif compatibility is False:
                self.restore_strict_producer_validation(quiet=True)

            self.refresh_current_view()
            self.status_var.set(
                f"Applied {written}/{len(records)} snapshot records"
                + (
                    " with unit-training compatibility state."
                    if compatibility is not None
                    else "."
                )
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def import_mapping(self) -> None:
        filename = filedialog.askopenfilename(
            title="Import final confirmed mapping",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return

        try:
            self.db.load_mapping(Path(filename))
            self.mapping_path = Path(filename)
            self.selected_card = None
            self.selected_row = None
            self.selected_rva = None
            self.session_originals = {}

            self.populate_card_tree()
            if self.process.handle:
                self.capture_session_originals()

            self.status_var.set(
                f"Loaded mapping {Path(filename).name}: "
                f"{len(self.db.effective)} cards, "
                f"{len(self.db.all_unique_rvas())} unique records."
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def export_mapping_copy(self) -> None:
        filename = filedialog.asksaveasfilename(
            title="Export mapping copy",
            defaultextension=".json",
            initialfile="War2_Remaster_Command_Cards_Final.json",
            filetypes=[("JSON", "*.json")],
        )
        if not filename:
            return

        Path(filename).write_text(
            json.dumps(self.db.mapping, indent=2),
            encoding="utf-8",
        )
        self.status_var.set(f"Exported {filename}.")

    def generate_cheat_engine_table(self) -> None:
        filename = filedialog.asksaveasfilename(
            title="Generate Cheat Engine table",
            defaultextension=".ct",
            initialfile="War2_Remaster_All_Command_Cards.ct",
            filetypes=[("Cheat Engine Table", "*.ct")],
        )
        if not filename:
            return

        root = ET.Element(
            "CheatTable",
            {"CheatEngineTableVersion": "45"},
        )
        entries = ET.SubElement(root, "CheatEntries")
        next_id = 0

        def add_text(parent, tag, value=None, **attrs):
            element = ET.SubElement(parent, tag, attrs)
            if value is not None:
                element.text = str(value)
            return element

        def add_group(parent, description):
            nonlocal next_id
            entry = ET.SubElement(parent, "CheatEntry")
            add_text(entry, "ID", next_id)
            next_id += 1
            add_text(entry, "Description", f'"{description}"')
            ET.SubElement(entry, "Options", {"moHideChildren": "1"})
            add_text(entry, "GroupHeader", "1")
            return ET.SubElement(entry, "CheatEntries")

        def add_value(
            parent,
            description,
            variable_type,
            address,
            byte_length=None,
        ):
            nonlocal next_id
            entry = ET.SubElement(parent, "CheatEntry")
            add_text(entry, "ID", next_id)
            next_id += 1
            add_text(entry, "Description", f'"{description}"')
            add_text(entry, "ShowAsHex", "1")
            add_text(entry, "VariableType", variable_type)
            if byte_length is not None:
                add_text(entry, "ByteLength", byte_length)
            add_text(entry, "Address", address)

        compatibility_group = add_group(
            entries,
            "Confirmed Unit-Training Compatibility",
        )
        add_value(
            compatibility_group,
            "Producer mismatch branch (75 29 strict / 90 90 compatible)",
            "Array of byte",
            f'"Warcraft II.exe"+{PRODUCER_MISMATCH_BRANCH_RVA:X}',
            2,
        )
        add_value(
            compatibility_group,
            "Peasant expected producer",
            "Byte",
            f'"Warcraft II.exe"+{PRODUCER_TABLE_RVA + 0x02:X}',
        )
        add_value(
            compatibility_group,
            "Peon expected producer",
            "Byte",
            f'"Warcraft II.exe"+{PRODUCER_TABLE_RVA + 0x03:X}',
        )

        for array_name in sorted(
            self.db.effective,
            key=lambda name: self.db.friendly(name),
        ):
            card_group = add_group(
                entries,
                f"{self.db.friendly(array_name)} [{array_name}]",
            )

            source_rows = self.db.source_rows(array_name)

            for row_index, rva in sorted(
                self.db.effective[array_name].items()
            ):
                source = source_rows[row_index]
                row_group = add_group(
                    card_group,
                    (
                        f"Button {row_index + 1} | Slot {source[0]} | "
                        f"{source[1]} | {source[3]} | +0x{rva:08X}"
                    ),
                )

                for name, label, offset, fmt in FIELD_LAYOUT:
                    variable_type = {
                        "B": "Byte",
                        "H": "2 Bytes",
                        "I": "4 Bytes",
                    }[fmt]

                    add_value(
                        row_group,
                        label,
                        variable_type,
                        f'"Warcraft II.exe"+{rva + offset:X}',
                    )

        ET.SubElement(root, "UserdefinedSymbols")
        ET.SubElement(root, "LuaScript").text = ""

        xml_bytes = minidom.parseString(
            ET.tostring(root, encoding="utf-8")
        ).toprettyxml(indent="  ", encoding="utf-8")

        Path(filename).write_bytes(xml_bytes)
        self.status_var.set(
            f"Generated Cheat Engine table with {next_id} entries."
        )

    def close(self) -> None:
        self.process.close()
        self.destroy()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This editor only runs on Windows.")

    CommandCardEditor().mainloop()


if __name__ == "__main__":
    main()
