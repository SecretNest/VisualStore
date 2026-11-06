"""Build a tiny ImageStore v1 database the way .NET would have written it (mixed-endian Guids)."""

import sqlite3
import uuid
from pathlib import Path

SCHEMA = (Path(__file__).parent / "v1_schema.sql").read_text()


def guid() -> uuid.UUID:
    return uuid.uuid4()


def le(u: uuid.UUID) -> bytes:
    return u.bytes_le


def build(path: Path) -> dict:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    archive, inbox = guid(), guid()
    jpg, png, mp4, zip_ = guid(), guid(), guid(), guid()
    f_a, f_b, f_c, f_d, f_gone = guid(), guid(), guid(), guid(), guid()
    sha_dup, sha_other = b"\x11" * 20, b"\x22" * 20
    conn.executemany(
        "INSERT INTO Folder VALUES (?, ?, ?, ?, ?)",
        [(le(archive), "Archive", r"D:\Images", 1, 1), (le(inbox), "Inbox", r"E:\New", 0, 0)],
    )
    conn.executemany(
        "INSERT INTO Extension VALUES (?, ?, ?, ?)",
        [
            (le(jpg), "jpg", 1, 0),
            (le(png), "PNG", 1, 0),
            (le(mp4), "mp4", 0, 0),
            (le(zip_), "zip", 0, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO File VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (le(f_a), le(archive), r"Set A\Sub", "a", le(jpg), None, sha_dup, 100, 255, 0.5),
            (le(f_b), le(inbox), "", "b", le(jpg), None, sha_dup, 100, 255, None),
            (le(f_c), le(inbox), "", "c", le(png), None, sha_other, 7, 255, None),
            (le(f_d), le(inbox), "clips", "d", le(mp4), None, None, -1, 2, None),
        ],
    )
    conn.execute(
        "INSERT INTO IgnoredDirectory VALUES (?, ?, ?, ?)", (le(guid()), le(archive), r"Thumbs", 1)
    )
    conn.executemany(
        "INSERT INTO SameFile VALUES (?, ?, ?, ?)",
        [(le(guid()), sha_dup, le(f_a), 0), (le(guid()), sha_dup, le(f_b), 1)],
    )
    conn.executemany(
        "INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)",
        [(le(guid()), le(f_a), le(f_c), 0.01, 1), (le(guid()), le(f_c), le(f_gone), 0.02, 0)],
    )
    conn.commit()
    conn.close()
    return {
        "archive": archive,
        "inbox": inbox,
        "jpg": jpg,
        "mp4": mp4,
        "zip": zip_,
        "f_a": f_a,
        "f_b": f_b,
        "f_c": f_c,
        "f_d": f_d,
    }
