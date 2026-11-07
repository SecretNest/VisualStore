"""Bulk application of the default rules over a queue filter (design §8.4). Pure."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from visualstore.domain.resolution import Member, preselect, propagate_keep


def signature(member_ids: Iterable[bytes]) -> str:
    """Identifies a group's member set so execution can detect a group that changed."""
    return hashlib.sha1(b"".join(sorted(member_ids))).hexdigest()


@dataclass(frozen=True)
class GroupPlan:
    group_id: bytes
    signature: str
    delete: tuple[bytes, ...]
    keep: tuple[bytes, ...]
    folders: tuple[str, ...]  # names of the Folders that lose files


@dataclass(frozen=True)
class BulkPlan:
    groups: tuple[GroupPlan, ...]
    matched: int
    skipped: int
    files_to_delete: int
    folders: tuple[str, ...]
    token: str


def plan_group(
    group_id: bytes,
    members: Sequence[Member],
    folder_names: dict[bytes, str],
    *,
    kept_edges: Iterable[tuple[bytes, bytes]] = (),
) -> GroupPlan | None:
    """The default resolution for one group, or None when the rules leave a member undecided,
    delete nothing, or would delete an end of a `related_keep` edge (a human said keep both):
    such groups are skipped by a bulk run (Global Constraints)."""
    # all_identical only matters for automatic execution, which a bulk run never does
    pre = preselect(members, all_identical=False, auto_execute_identical=False)
    if any(d == "undecided" for d in pre.decisions.values()):
        return None
    decisions = propagate_keep(pre.decisions, kept_edges)
    if decisions != pre.decisions:
        return None
    order = sorted(members, key=lambda m: m.relative_path)
    delete = tuple(m.file_id for m in order if decisions[m.file_id] == "delete")
    keep = tuple(m.file_id for m in order if decisions[m.file_id] == "keep")
    if not delete:
        return None
    folders = tuple(
        sorted({folder_names[m.folder_id] for m in members if decisions[m.file_id] == "delete"})
    )
    return GroupPlan(group_id, signature(m.file_id for m in members), delete, keep, folders)


def build_plan(matched: int, plans: Iterable[GroupPlan | None]) -> BulkPlan:
    plans = list(plans)
    groups = tuple(sorted((p for p in plans if p is not None), key=lambda p: p.group_id))
    token_source = "\n".join(
        f"{p.group_id.hex()}:{p.signature}:{','.join(d.hex() for d in p.delete)}" for p in groups
    )
    return BulkPlan(
        groups=groups,
        matched=matched,
        skipped=sum(1 for p in plans if p is None),
        files_to_delete=sum(len(p.delete) for p in groups),
        folders=tuple(sorted({f for p in groups for f in p.folders})),
        token=hashlib.sha1(token_source.encode()).hexdigest(),
    )
