"""Groups are connected components of the pair graph (design §7.5). Pure union-find."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class PairEdge:
    a: bytes
    b: bytes
    distance: float | None
    source: str  # sha1 | vector | import
    judgment: str  # none | related_keep | unrelated


@dataclass(frozen=True)
class Component:
    members: frozenset[bytes]
    min_distance: float | None
    has_identical: bool


def is_edge(edge: PairEdge, group_distance: float) -> bool:
    if edge.judgment == "unrelated":
        return False
    if edge.source == "sha1":
        return True
    return edge.distance is not None and edge.distance <= group_distance


def components(
    files: Iterable[bytes], edges: Iterable[PairEdge], group_distance: float
) -> list[Component]:
    parent: dict[bytes, bytes] = {f: f for f in files}

    def find(x: bytes) -> bytes:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: bytes, y: bytes) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    live = [e for e in edges if is_edge(e, group_distance)]
    for e in live:
        parent.setdefault(e.a, e.a)
        parent.setdefault(e.b, e.b)
        union(e.a, e.b)

    members: dict[bytes, set[bytes]] = {}
    for f in parent:
        members.setdefault(find(f), set()).add(f)
    stats: dict[bytes, tuple[float | None, bool]] = {}
    for e in live:
        root = find(e.a)
        min_d, ident = stats.get(root, (None, False))
        d = 0.0 if e.source == "sha1" else e.distance
        if d is not None and (min_d is None or d < min_d):
            min_d = d
        stats[root] = (min_d, ident or e.source == "sha1")

    out = [
        Component(frozenset(ms), *stats.get(root, (None, False)))
        for root, ms in members.items()
        if len(ms) > 1
    ]
    out.sort(key=lambda c: min(c.members))
    return out
