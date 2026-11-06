"""Default resolution rules for a duplicate group (design §8.2). Pure."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from visualstore.domain.paths import path_key


@dataclass(frozen=True)
class Member:
    file_id: bytes
    folder_id: bytes
    role: str  # archive | inbox
    folder_priority: int
    relative_path: str


@dataclass(frozen=True)
class Preselection:
    decisions: dict[bytes, str]
    status: str  # open | auto_resolved
    auto_executable: bool


def preselect(
    members: Sequence[Member], *, all_identical: bool, auto_execute_identical: bool
) -> Preselection:
    archive = [m for m in members if m.role == "archive"]
    inbox = [m for m in members if m.role != "archive"]
    decisions: dict[bytes, str] = {}
    automatic_allowed = all_identical and auto_execute_identical

    if archive and inbox:
        for m in archive:
            decisions[m.file_id] = "keep"
        for m in inbox:
            decisions[m.file_id] = "delete"
        auto = automatic_allowed
    elif inbox:  # inbox only: keep the first by path, a human must confirm
        ordered = sorted(inbox, key=lambda m: path_key(m.relative_path))
        decisions[ordered[0].file_id] = "keep"
        for m in ordered[1:]:
            decisions[m.file_id] = "delete"
        auto = False
    else:  # archive only
        folders = {m.folder_id for m in archive}
        if len(folders) == 1:
            for m in archive:
                decisions[m.file_id] = "undecided"
            auto = False
        else:
            best = min(
                archive, key=lambda m: (m.folder_priority, path_key(m.relative_path))
            ).folder_id
            for m in archive:
                decisions[m.file_id] = "keep" if m.folder_id == best else "delete"
            auto = automatic_allowed

    status = "auto_resolved" if auto else "open"
    return Preselection(decisions=decisions, status=status, auto_executable=auto)


def propagate_keep(
    decisions: dict[bytes, str], kept_edges: Iterable[tuple[bytes, bytes]]
) -> dict[bytes, str]:
    """A human said "keep both" for each kept edge: spread `keep` along them. Returns a new
    dict; the input is untouched."""
    out = dict(decisions)
    edges = list(kept_edges)
    changed = True
    while changed:
        changed = False
        for a, b in edges:
            for x, y in ((a, b), (b, a)):
                if out.get(x) == "keep" and out.get(y) != "keep":
                    out[y] = "keep"
                    changed = True
    return out
