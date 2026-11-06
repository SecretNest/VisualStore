"""Nearest group member for every member (design §8.3: distance to the nearest member). Pure."""

from __future__ import annotations

from collections.abc import Iterable

from visualstore.domain.paths import path_key


def nearest(
    members: dict[bytes, str], pairs: Iterable[tuple[bytes, bytes, float | None, str]]
) -> dict[bytes, tuple[bytes, float] | None]:
    """`members` maps file id -> relative path; `pairs` are (a, b, distance, judgment).

    Per member: the other member at the smallest distance (sha1 pairs count as 0.0; ties go to
    the neighbour whose path sorts first), or None when no usable pair touches it."""
    best: dict[bytes, tuple[float, str, bytes]] = {}
    for a, b, distance, judgment in pairs:
        if judgment == "unrelated" or a == b or a not in members or b not in members:
            continue
        d = 0.0 if distance is None else float(distance)
        for me, other in ((a, b), (b, a)):
            candidate = (d, path_key(members[other]), other)
            if me not in best or candidate < best[me]:
                best[me] = candidate
    return {m: ((best[m][2], best[m][0]) if m in best else None) for m in members}
