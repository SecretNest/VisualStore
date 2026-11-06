"""Frame sampling rules (design §7.3). Pure: no pyvips, no ffmpeg."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

KEYFRAME_MERGE_S = 0.05


@dataclass
class Frame:
    index: int
    time_ms: int
    image: Any  # a decoded pyvips image (see media.decode.Decoded.image)


def sample_indices(count: int, max_frames: int) -> list[int]:
    """All frames up to the cap, otherwise `max_frames` evenly spaced indices 0..count-1."""
    if count <= 0:
        return []
    if count <= max_frames:
        return list(range(count))
    if max_frames <= 1:
        return [0]
    return [round(i * (count - 1) / (max_frames - 1)) for i in range(max_frames)]


def sample_times(duration_s: float, max_frames: int, first_keyframe_s: float | None) -> list[float]:
    """One frame per second up to the cap, otherwise `max_frames` midpoints plus the first
    keyframe (when it is not within 50 ms of a sampled time). Times in seconds, ms precision."""
    if duration_s <= 0:
        return [0.0]
    if duration_s <= max_frames:
        times = [float(t) for t in range(math.ceil(duration_s))]
    else:
        times = [(i + 0.5) * duration_s / max_frames for i in range(max_frames)]
        if first_keyframe_s is not None and all(
            abs(first_keyframe_s - t) > KEYFRAME_MERGE_S for t in times
        ):
            times.append(float(first_keyframe_s))
    return sorted(round(t, 3) for t in times)


def sampling_params(settings: dict) -> dict:
    """What a compute child needs to know about sampling: plain data for the job params."""
    return {
        "max_per_file": int(settings.get("frames.max_per_file", 8)),
        "ffmpeg_timeout_s": int(settings.get("media.ffmpeg_timeout_s", 120)),
    }
