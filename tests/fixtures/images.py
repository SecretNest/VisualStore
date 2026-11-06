"""Image fixtures without any imaging library: PNG is a zlib stream plus CRC'd chunks."""

import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return struct.pack(">I", len(data)) + kind + data + crc


def png_header_only(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)


def png_bytes(width: int, height: int, rgb=(200, 30, 30), gradient: bool = False) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type none
        for x in range(width):
            if gradient:
                rows.extend((x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1), 128))
            else:
                rows.extend(rgb)
    idat = zlib.compress(bytes(rows), 6)
    return png_header_only(width, height) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def garbage() -> bytes:
    return b"PK\x03\x04 this is really a zip archive, not a picture " * 20
