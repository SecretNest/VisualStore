"""Relative-path rules shared by scan, import and the API (design §5.1 File, §5.3).

Paths in the database are '/'-separated and relative to the Folder; '' is the root. Comparisons
fold ASCII case only, exactly like SQLite's COLLATE NOCASE, so Python and the unique index agree.
"""

from __future__ import annotations

TRASH_DIR = ".visualstore-trash"


def normalise_relative(text: str) -> str:
    """'/'-separated, no leading/trailing separators, '' for the root; backslashes become '/'."""
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("relative paths may not contain '..'")
    return "/".join(parts)


def split_relative(rel: str) -> tuple[str, str]:
    directory, _, name = rel.rpartition("/")
    return directory, name


def join_relative(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def path_key(text: str) -> str:
    """ASCII-only case folding (what SQLite NOCASE does)."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def is_under(rel: str, ignored: str, recursive: bool) -> bool:
    a, b = path_key(rel), path_key(ignored)
    if a == b:
        return True
    return recursive and a.startswith(b + "/")
