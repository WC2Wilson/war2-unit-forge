from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

SUPPORTED_IMAGE_SIZE = 0x62B000
PREFERRED_IMAGE_BASE = 0x00400000
UNITDATA_DESCRIPTOR_RVA = 0x004C0150
UNITDATA_DESCRIPTOR_COUNT = 32
KNOWN_TIMESTAMPS = {
    0x6813B7ED: "Warcraft II Remastered 1.0.2.2505 (installed)",
    0x681446FD: "Warcraft II Remastered 1.0.2.2505 (uploaded copy)",
    0x699E13E7: "Warcraft II Remastered 1.0.2.2818",
}

SELECTED_OBJECT_POINTER_RVA = 0x0051CC40
SELECTED_CARD_ID_OFFSET = 0x25
SELECTED_UNIT_TYPE_OFFSET = 0x27
SELECTED_UNIT_GROUP_OFFSET = 0x2B
SELECTED_OWNER_OFFSET = 0x2C

UNIT_ARRAY_RVA = 0x0051C704
MAX_UNITS_RVA = 0x0051BFB8
UNIT_RECORD_SIZE = 152
UNIT_CREATE_KNOWN_RVA = 0x000EDB10
UNIT_CREATE_SIGNATURE = b"\x55\x8B\xEC\x83\xEC\x0C\x8A\x45\x14\x56\x50\x89\x45\xF8"


@dataclass(frozen=True)
class PEFingerprint:
    path: str
    valid_pe: bool
    timestamp: int = 0
    image_size: int = 0
    sha256: str = ""
    profile: str = ""

    @property
    def supported(self) -> bool:
        return self.valid_pe and self.image_size == SUPPORTED_IMAGE_SIZE and self.timestamp in KNOWN_TIMESTAMPS


def fingerprint(path: str | Path) -> PEFingerprint:
    file_path = Path(path)
    raw = file_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if len(raw) < 0x100 or raw[:2] != b"MZ":
        return PEFingerprint(str(file_path), False, sha256=sha, profile="Not a PE executable")
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if pe_offset + 0x80 > len(raw) or raw[pe_offset : pe_offset + 4] != b"PE\0\0":
        return PEFingerprint(str(file_path), False, sha256=sha, profile="Invalid PE header")
    timestamp = struct.unpack_from("<I", raw, pe_offset + 8)[0]
    optional = pe_offset + 24
    image_size = struct.unpack_from("<I", raw, optional + 56)[0]
    name = KNOWN_TIMESTAMPS.get(timestamp, "Unknown Warcraft II Remastered build")
    if image_size != SUPPORTED_IMAGE_SIZE:
        name += f"; unexpected image size 0x{image_size:X}"
    return PEFingerprint(str(file_path), True, timestamp, image_size, sha, name)


def _path_bases(selected: str | Path, max_ancestors: int = 5) -> list[Path]:
    r"""Return the selected folder plus a small, duplicate-free ancestor chain.

    A normal remaster install separates the executable and DAT files like this::

        x86\Warcraft II.exe
        x86\Data\Rez\unitdata.dat

    Users may therefore select the EXE, x86, Data, or Data\Rez.  Walking a
    bounded ancestor chain lets both resolvers recover the sibling location
    without recursively scanning Program Files.
    """
    selected_path = Path(selected).expanduser()
    start = selected_path.parent if selected_path.is_file() else selected_path
    bases: list[Path] = []
    current = start
    for _ in range(max_ancestors + 1):
        if current not in bases:
            bases.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return bases


def locate_executable(selected: str | Path) -> Path | None:
    selected_path = Path(selected).expanduser()

    # An explicitly selected EXE is authoritative, including renamed copies
    # such as "Warcraft II(5).exe" used for analysis.
    if selected_path.is_file() and selected_path.suffix.lower() == ".exe":
        return selected_path

    candidates: list[Path] = []
    for base in _path_bases(selected_path):
        candidates.extend(
            [
                base / "Warcraft II.exe",
                base / "x86" / "Warcraft II.exe",
            ]
        )

    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return path
    return None


def locate_data_folder(selected: str | Path) -> Path | None:
    selected_path = Path(selected).expanduser()
    candidates: list[Path] = []
    for base in _path_bases(selected_path):
        candidates.extend(
            [
                base,
                base / "Rez",
                base / "Data",
                base / "Data" / "Rez",
                base / "x86" / "Data",
                base / "x86" / "Data" / "Rez",
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "unitdata.dat").is_file() and (candidate / "unitdato.dat").is_file():
            return candidate
    return None
