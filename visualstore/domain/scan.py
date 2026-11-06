"""Pure scan logic (design §7.1): compare a directory listing with the database and detect moves.

No I/O here. The scan job (jobs/io/scan.py) walks the disk, calls these, and writes the result.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from visualstore.domain.paths import TRASH_DIR, is_under, path_key


@dataclass(frozen=True)
class DiskEntry:
    directory: str
    name: str
    extension: str  # lowercase, no dot
    size: int
    mtime: int


@dataclass(frozen=True)
class DbFile:
    id: bytes
    directory: str
    name: str
    size: int
    mtime: int
    sha1: bytes | None


@dataclass
class ScanPlan:
    insert: list[DiskEntry] = field(default_factory=list)
    changed: list[tuple[bytes, DiskEntry]] = field(default_factory=list)
    unchanged: int = 0
    missing: list[DbFile] = field(default_factory=list)


def _key(directory: str, name: str) -> tuple[str, str]:
    return path_key(directory), path_key(name)


def diff(disk: Iterable[DiskEntry], db: Iterable[DbFile]) -> ScanPlan:
    plan = ScanPlan()
    by_key = {_key(f.directory, f.name): f for f in db}
    seen: set[tuple[str, str]] = set()
    for entry in disk:
        key = _key(entry.directory, entry.name)
        seen.add(key)
        existing = by_key.get(key)
        if existing is None:
            plan.insert.append(entry)
        elif existing.size == entry.size and existing.mtime == entry.mtime:
            plan.unchanged += 1
        else:
            plan.changed.append((existing.id, entry))
    plan.missing = [f for k, f in by_key.items() if k not in seen]
    return plan


def move_candidates(plan: ScanPlan) -> dict[int, tuple[list[DbFile], list[DiskEntry]]]:
    """Missing and inserted files bucketed by size, only for sizes present on both sides.
    Hashing is then limited to these files (design §7.1)."""
    missing_by_size: dict[int, list[DbFile]] = defaultdict(list)
    for f in plan.missing:
        missing_by_size[f.size].append(f)
    inserted_by_size: dict[int, list[DiskEntry]] = defaultdict(list)
    for e in plan.insert:
        inserted_by_size[e.size].append(e)
    return {
        size: (missing_by_size[size], inserted_by_size[size])
        for size in missing_by_size
        if size in inserted_by_size
    }


def resolve_moves(
    missing_sha1: dict[bytes, bytes], inserted_sha1: dict[tuple[str, str], bytes]
) -> list[tuple[bytes, tuple[str, str]]]:
    """A missing file moved when exactly one new file has the same sha1 and that new file
    matches exactly one missing file. Anything ambiguous is treated as delete + add."""
    new_by_sha: dict[bytes, list[tuple[str, str]]] = defaultdict(list)
    for loc, sha in inserted_sha1.items():
        new_by_sha[sha].append(loc)
    old_by_sha: dict[bytes, list[bytes]] = defaultdict(list)
    for fid, sha in missing_sha1.items():
        old_by_sha[sha].append(fid)
    moves = []
    for sha, old_ids in old_by_sha.items():
        new_locs = new_by_sha.get(sha, [])
        if len(old_ids) == 1 and len(new_locs) == 1:
            moves.append((old_ids[0], new_locs[0]))
    return moves


def should_skip_directory(rel_dir: str, ignored: list[tuple[str, bool]]) -> bool:
    if not rel_dir:
        return False
    if is_under(rel_dir, TRASH_DIR, recursive=True):
        return True
    return any(is_under(rel_dir, directory, recursive) for directory, recursive in ignored)
