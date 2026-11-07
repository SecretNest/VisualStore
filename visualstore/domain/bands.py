"""Threshold bands over cosine distance (design §7.6)."""

from __future__ import annotations

from dataclasses import dataclass

BANDS = ("identical", "near_duplicate", "variant", "candidate")


@dataclass(frozen=True)
class Thresholds:
    near_duplicate: float
    variant: float


def thresholds_from(settings: dict) -> Thresholds:
    return Thresholds(
        near_duplicate=float(settings["similarity.near_duplicate"]),
        variant=float(settings["similarity.variant"]),
    )


def band(distance: float | None, has_identical: bool, t: Thresholds) -> str:
    if has_identical:  # a byte-identical pair inside: the group is identical (as count_by_band)
        return "identical"
    if distance is None:
        return "candidate"
    if distance <= t.near_duplicate:
        return "near_duplicate"
    if distance <= t.variant:
        return "variant"
    return "candidate"


def band_bounds(name: str, t: Thresholds) -> tuple[float, float]:
    """(lo, hi] on min_distance for the non-identical bands; identical is has_identical = 1."""
    if name == "identical":
        return (float("-inf"), 0.0)
    if name == "near_duplicate":
        return (0.0, t.near_duplicate)
    if name == "variant":
        return (t.near_duplicate, t.variant)
    if name == "candidate":
        return (t.variant, float("inf"))
    raise ValueError(f"band must be one of {BANDS}")
