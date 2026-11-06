"""Video probing and frame extraction with ffmpeg/ffprobe (design §7.2, §7.3, ADR-0007).
Frames come back over a pipe as PNG; no temp files."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction

from visualstore.media.decode import image_from_png
from visualstore.media.errors import NotMedia, Unreadable
from visualstore.media.frames import Frame

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Library paths are user-controlled names: only the file protocol may be opened, the input is
# always passed as file:<absolute path>, and nothing is read from stdin.
_PROBE = ["-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file"]
_FFMPEG = ["-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe"]


def _input(path: str) -> str:
    return "file:" + os.path.abspath(path)


@dataclass(frozen=True)
class Probe:
    duration_s: float
    fps: float
    nb_frames: int | None
    first_keyframe_s: float | None
    width: int = 0
    height: int = 0


def _run(args: list[str], timeout_s: float, what: str) -> bytes:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(f"{what} timed out after {timeout_s}s") from exc
    except OSError as exc:  # binary missing
        raise Unreadable(f"{what} could not be started: {exc}") from exc
    if proc.returncode != 0 and not proc.stdout:
        message = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unreadable(f"{what} failed: {message[-1] if message else proc.returncode}")
    return proc.stdout


def probe(path: str, timeout_s: float) -> Probe:
    out = _run(
        [
            "ffprobe",
            *_PROBE,
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=nb_frames,r_frame_rate,avg_frame_rate,codec_type,width,height",
            "-of",
            "json",
            _input(path),
        ],
        timeout_s,
        "ffprobe",
    )
    try:
        info = json.loads(out or b"{}")
    except ValueError as exc:
        raise NotMedia("ffprobe output is not JSON") from exc
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise NotMedia("no video stream")
    stream = streams[0]
    try:
        duration = float(info.get("format", {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            fps = float(Fraction(stream.get(key) or "0/1"))
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        if fps > 0:
            break
    try:
        nb_frames = int(stream["nb_frames"]) if stream.get("nb_frames") else None
    except ValueError:
        nb_frames = None
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    return Probe(duration, fps or 25.0, nb_frames, _first_keyframe(path, timeout_s), width, height)


def _check_pixels(path: str, timeout_s: float, max_pixels: int) -> None:
    """Refuse oversized frames before ffmpeg decodes anything (design §7.2 pixel limit)."""
    out = _run(
        [
            "ffprobe",
            *_PROBE,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            _input(path),
        ],
        timeout_s,
        "ffprobe",
    )
    try:
        width, height = (int(v) for v in out.decode().strip().splitlines()[0].split(",")[:2])
    except (ValueError, IndexError):
        return  # unknown dimensions: the PNG decode still enforces the cap
    if width * height > max_pixels:
        raise Unreadable(f"{width}x{height} exceeds the limit of {max_pixels} pixels")


def _first_keyframe(path: str, timeout_s: float) -> float | None:
    try:
        out = _run(
            [
                "ffprobe",
                *_PROBE,
                "-select_streams",
                "v:0",
                "-skip_frame",
                "nokey",
                "-read_intervals",
                "%+#1",
                "-show_entries",
                "frame=pts_time",
                "-of",
                "csv=p=0",
                _input(path),
            ],
            timeout_s,
            "ffprobe",
        )
        first = out.decode().strip().splitlines()
        return float(first[0].split(",")[0]) if first and first[0] else None
    except (Unreadable, ValueError):
        return None


def count_frames(path: str, timeout_s: float) -> int:
    out = _run(
        [
            "ffprobe",
            *_PROBE,
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            _input(path),
        ],
        timeout_s,
        "ffprobe",
    )
    try:
        return int(out.decode().strip().splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise NotMedia("ffprobe could not count frames") from exc


def frame_png(path: str, time_s: float, timeout_s: float) -> bytes:
    data = _run(
        [
            "ffmpeg",
            *_FFMPEG,
            "-ss",
            f"{time_s:.3f}",
            "-i",
            _input(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        timeout_s,
        "ffmpeg",
    )
    if not data.startswith(PNG_SIGNATURE):
        raise Unreadable(f"no frame at {time_s:.3f}s")
    return data


def frames_at_times(
    path: str, times_s: list[float], timeout_s: float, max_pixels: int
) -> list[Frame]:
    _check_pixels(path, timeout_s, max_pixels)
    frames: list[Frame] = []
    for t in times_s:
        try:
            data = frame_png(path, t, timeout_s)
        except Unreadable as exc:
            if "timed out" in str(exc) or "could not be started" in str(exc):
                raise
            continue  # beyond the decodable part of a damaged file: skip this time
        decoded = image_from_png(data, max_pixels)
        frames.append(Frame(len(frames), round(t * 1000), decoded.image))
    if not frames:
        raise Unreadable("no decodable frames")
    return frames


def _split_pngs(data: bytes) -> list[bytes]:
    parts = data.split(PNG_SIGNATURE)
    return [PNG_SIGNATURE + p for p in parts[1:]]


def frames_at_indices(
    path: str, indices: list[int], timeout_s: float, max_pixels: int, fps: float
) -> list[Frame]:
    if not indices:
        return []
    _check_pixels(path, timeout_s, max_pixels)
    select = "+".join(f"eq(n\\,{i})" for i in indices)
    data = _run(
        [
            "ffmpeg",
            *_FFMPEG,
            "-i",
            _input(path),
            "-vf",
            f"select='{select}'",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            str(len(indices)),
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        timeout_s,
        "ffmpeg",
    )
    pngs = _split_pngs(data)
    if not pngs:
        raise Unreadable("no decodable frames")
    frames = []
    for position, (index, png) in enumerate(zip(indices, pngs, strict=False)):
        decoded = image_from_png(png, max_pixels)
        frames.append(Frame(position, round(index / fps * 1000) if fps else 0, decoded.image))
    return frames
