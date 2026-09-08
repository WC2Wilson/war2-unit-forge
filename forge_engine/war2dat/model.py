from __future__ import annotations

from pathlib import Path
import struct

TYPES = {
    "u8": ("<B", 1), "s8": ("<b", 1),
    "u16": ("<H", 2), "s16": ("<h", 2),
    "u32": ("<I", 4), "s32": ("<i", 4),
}


class DatFile:
    """Read-only DAT reference used to label and decode live runtime tables.

    Unit Forge never saves or patches the user's DAT files. Runtime changes are
    applied only to the attached Warcraft II process.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()

    def read(self, offset: int, value_type: str) -> int:
        fmt, size = TYPES[value_type]
        if offset < 0 or offset + size > len(self.data):
            raise IndexError("value extends beyond the file")
        return struct.unpack_from(fmt, self.data, offset)[0]


def parse_number(text: str) -> int:
    text = text.strip().lower().replace("_", "")
    if text.startswith("-"):
        return -parse_number(text[1:])
    if text.startswith("0x"):
        return int(text, 16)
    return int(text, 10)


def find_values(data: bytes | bytearray, value_type: str, value: int) -> list[int]:
    fmt, size = TYPES[value_type]
    needle = struct.pack(fmt, value)
    return [i for i in range(0, len(data) - size + 1) if data[i:i + size] == needle]
