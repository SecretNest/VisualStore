"""Synthetic media generated with ffmpeg at test time (skipped where ffmpeg is absent)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def require_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        if os.environ.get("VISUALSTORE_TEST_REQUIRE_MEDIA") == "1":
            raise RuntimeError("ffmpeg/ffprobe are required here but missing")
        pytest.skip("ffmpeg/ffprobe not available", allow_module_level=True)


def _run(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


def make_clip(
    path: Path, seconds: float, size: str = "160x120", rate: int = 10, colour: str | None = None
) -> Path:
    source = (
        f"color=c={colour}:size={size}:rate={rate}"
        if colour
        else f"testsrc=size={size}:rate={rate}"
    )
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            str(seconds),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )
    return path


def make_gif(path: Path, frames: int, rate: int = 5, size: str = "64x48") -> Path:
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}",
            "-frames:v",
            str(frames),
            str(path),
        ]
    )
    return path


def make_apng(path: Path, frames: int, rate: int = 5, size: str = "32x24") -> Path:
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}",
            "-frames:v",
            str(frames),
            "-f",
            "apng",
            str(path),
        ]
    )
    return path


def make_webp_anim(path: Path, frames: int, rate: int = 5, size: str = "64x48") -> Path:
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}",
            "-frames:v",
            str(frames),
            "-c:v",
            "libwebp_anim",
            "-loop",
            "0",
            str(path),
        ]
    )
    return path


def truncate_file(path: Path, keep_fraction: float) -> Path:
    data = path.read_bytes()
    path.write_bytes(data[: int(len(data) * keep_fraction)])
    return path
