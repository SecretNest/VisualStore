"""Animated images: gif/webp pages with pyvips, APNG through ffmpeg (design §7.2, ADR-0007)."""

from __future__ import annotations

from pathlib import PurePath

import pyvips

from visualstore.media.decode import _NOT_MEDIA_MARKERS, FRAME_PX, normalise
from visualstore.media.errors import NotMedia, Unreadable
from visualstore.media.frames import Frame, sample_indices


def backend(path: str) -> str:
    return "ffmpeg" if PurePath(path).suffix.lower() == ".apng" else "vips"


def _open(path: str, **options) -> pyvips.Image:
    try:
        source = pyvips.Source.new_from_file(path)
        return pyvips.Image.new_from_source(
            source, "", access="sequential", fail_on="truncated", **options
        )
    except pyvips.Error as exc:
        message = str(exc)
        if any(marker in message for marker in _NOT_MEDIA_MARKERS):
            raise NotMedia(message) from exc
        raise Unreadable(message) from exc


def page_count(path: str, max_pixels: int) -> int:
    img = _open(path)
    if img.width * img.height > max_pixels:
        raise Unreadable(f"{img.width}x{img.height} exceeds the limit of {max_pixels} pixels")
    return int(img.get("n-pages")) if img.get_typeof("n-pages") else 1


def dimensions(path: str) -> tuple[int, int]:
    """Width and height of one page (the source size, before any shrinking)."""
    img = _open(path, page=0, n=1)
    return int(img.width), int(img.height)


def page_times_ms(path: str) -> list[int]:
    img = _open(path)
    if not img.get_typeof("delay"):
        return [0]
    delays = [max(1, int(d)) for d in img.get("delay")]
    times, t = [], 0
    for d in delays:
        times.append(t)
        t += d
    return times or [0]


def load_pages(path: str, indices: list[int], max_pixels: int) -> list[Frame]:
    times = page_times_ms(path)
    frames: list[Frame] = []
    for position, index in enumerate(indices):
        img = _open(path, page=index, n=1)
        if img.width * img.height > max_pixels:
            raise Unreadable(f"{img.width}x{img.height} exceeds the limit of {max_pixels} pixels")
        if index < len(times):
            time_ms = times[index]
        else:  # the delay list is shorter than the page count: extend with the last known step
            step = (times[-1] - times[-2]) if len(times) > 1 else 100
            time_ms = times[-1] + step * (index - len(times) + 1)
        frames.append(Frame(position, time_ms, normalise(img, FRAME_PX)))
    return frames


def frames(path: str, max_frames: int, timeout_s: float, max_pixels: int) -> list[Frame]:
    """The sampled frames of an animated image through the right backend."""
    if backend(path) == "ffmpeg":
        from visualstore.media.video import count_frames, frames_at_indices, probe

        count = count_frames(path, timeout_s)
        info = probe(path, timeout_s)
        return frames_at_indices(
            path,
            sample_indices(count, max_frames),
            timeout_s,
            max_pixels,
            info.fps,
            (info.width, info.height),
        )
    count = page_count(path, max_pixels)
    return load_pages(path, sample_indices(count, max_frames), max_pixels)
