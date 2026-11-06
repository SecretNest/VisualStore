# Milestone 5 · Video and animated images — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> This plan was expanded from the task-level plan of the same name after milestone 4 shipped. The "Decide before expanding" items are decided as recorded in [ADR-0007](../decisions/0007-m5-implementation-choices.md) (written in Task 1): frames come out of ffmpeg over a pipe (no temp files), one call per sampled time for video and one `select` call for index-based sources; ffprobe gives duration, frame count and the first keyframe; animated gif/webp pages are read one at a time with pyvips, APNG goes through ffmpeg; no `media.max_frames_decoded` setting is needed because pages are never loaded in bulk.

**Goal:** Animated images and videos take part in similarity with several sampled frames each, stills match video frames, deletions and re-fingerprints keep frame rows and index keys consistent, and the group detail page shows a frame strip for multi-frame members whose frames can be put into the comparison pane.

**Architecture:** `media/frames.py` (pure) decides which frame indices or times to sample. `media/video.py` drives `ffprobe`/`ffmpeg` with `-nostdin` and a timeout and returns decoded frames as pyvips images; `media/animated.py` reads gif/webp pages with pyvips (`page=i, n=1`) and routes APNG to ffmpeg. The `fingerprint` handler grows `animated` and `video` branches (and detects animated webp by page count), embeds all frames of a file in one batch and returns `frames: [{index, time_ms, vector}]`; the sink already stores per-frame rows and per-frame index keys, and `allocate_keys` now retires keys of frames that disappeared. A frames API lists a file's frames and renders one frame on demand into `previews/frames/`. The frontend gets a `FrameStrip` whose clicks switch the comparison image to that frame.

**Tech Stack:** Python 3.12, pyvips, ffmpeg/ffprobe (already in the image and in CI), numpy, FastAPI, React + TypeScript, vitest, Playwright. No new dependencies. Media tests run in the `visualstore:test` image (the host has neither libvips nor ffmpeg).

**Spec:** [05 §7.2, §7.3, §7.4](../design/05-pipeline.md), [03 §5.1 Fingerprint and Extension](../design/03-data-model.md), [06 §8.3 frame strip](../design/06-review-ui.md#83-group-detail), [07 §9.5](../design/07-operations.md#95-resources), [11 §13 item 5](../design/11-roadmap.md#13-milestones).

## Global Constraints

- Everything in `CLAUDE.md`: single English docs tree, Conventional Commits with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer, UI copy in English.
- Sampling (settings): `frames.max_per_file = 8`; animated ≤ `max` frames → all, else `max` evenly spaced; video ≤ `max` seconds → 1 fps, else `max` evenly spaced plus the first keyframe; every frame records `frame_time_ms`.
- ffmpeg and ffprobe always run with `-nostdin`, `-hide_banner`, `-loglevel error` and the timeout `media.ffmpeg_timeout_s` (default 120); a timeout marks the file `unreadable` with the reason, never hangs the child; no temp files are created.
- A webp (or any `image` entry) whose content has more than one page is treated as `animated` regardless of the extension table (design §5.1).
- Deleting a file removes every frame row and marks every index key; re-fingerprinting replaces the frame set (rows and keys) instead of appending.
- Every FastAPI handler is `async def`; ffmpeg/pyvips work in handlers goes through `asyncio.to_thread`; pyvips stays a lazy import in `api/files.py`.
- `ruff check` / `ruff format --check` clean; `cd web && npm run lint && npm test && npm run build` clean; e2e green.

## Review Focus

1. **A video with a corrupt tail (truncated file)** must yield the frames ffmpeg can still decode and end `fingerprinted`, or `unreadable` with a reason when none decode; never leave temp files behind. (Task 2.)
2. **A one-frame GIF, a ten-frame GIF and a 500-frame APNG** must produce 1, 8 and 8 frames with strictly increasing `frame_time_ms`. (Task 3.)
3. **A still that is a frame of a video** must land within the near-duplicate band of the video (same vector space, §7.3) through the real fingerprint → match → sink path. (Task 5.)
4. **Deleting a video** must remove every frame row and index key; **re-fingerprinting with fewer frames** must replace the frame set and retire the keys of vanished frames (so `match` removes them from the index). (Task 4.)
5. **The frame strip** must show frame times and switch the comparison image on click without re-downloading all frames; a frame that cannot be rendered falls back like a missing preview. (Tasks 6 and 7.)

---

## File structure

```
visualstore/
  media/frames.py               Frame dataclass; sample_indices(count, max); sample_times(duration_s, max, first_keyframe_s); sampling_params(settings)
  media/video.py                Probe; probe(path, timeout_s); frames_at_times(path, times_s, timeout_s); frames_at_indices(path, indices, timeout_s, fps); frame_png(path, time_s, timeout_s)
  media/animated.py             page_count(path); page_times_ms(path); load_pages(path, indices, max_pixels); backend(path) -> "vips" | "ffmpeg"
  media/decode.py               Decoded.pages; normalise(img); image_from_png(data, max_pixels)
  jobs/compute/handlers.py      animated/video branches, batch embedding, middle-frame thumbnail for video
  db/fingerprints.py            allocate_keys retires keys of frames not in the new set
  db/groups.py                  members carry media_type
  db/settings.py                frames.max_per_file, media.ffmpeg_timeout_s
  jobs/io/scan.py, model_switch.py, actions.py   fingerprint params gain "frames"
  api/files.py                  GET /files/{id}/frames, GET /files/{id}/frame/{index}; shared cached-render helper
  jobs/results.py               sink drops previews/frames/{id}-*.webp with the thumbnail
tests/fixtures/media.py         require_ffmpeg(); make_clip(); make_gif(); make_apng(); make_webp_anim(); truncate_file()
web/src/components/FrameStrip.tsx
web/src/components/Compare.tsx  frame-aware image source
web/src/pages/GroupDetail.tsx   strips for selected and target members; picked frames
web/e2e/start-server.sh         navy clip + frame-0 still replace the blue pair
docs/decisions/0007-m5-implementation-choices.md
```

---

### Task 1: ADR-0007, sampling rules, settings and params

**Files:**
- Create: `docs/decisions/0007-m5-implementation-choices.md`, `visualstore/media/frames.py`, `tests/unit/test_frames_sampling.py`
- Modify: `docs/decisions/README.md`, `visualstore/db/settings.py`, `tests/unit/test_settings_db.py`

**Interfaces:**
- `Frame(index: int, time_ms: int, image: Any)` — `image` is a pyvips image (typed `Any` so the module imports without pyvips).
- `sample_indices(count: int, max_frames: int) -> list[int]`: `count <= 0` → `[]`; `count <= max_frames` → `list(range(count))`; else `max_frames` evenly spaced indices from `0` to `count - 1` (strictly increasing; `max_frames == 1` → `[0]`).
- `sample_times(duration_s: float, max_frames: int, first_keyframe_s: float | None) -> list[float]`: `duration_s <= 0` → `[0.0]`; `duration_s <= max_frames` → one per second `[0.0, 1.0, …]` up to `ceil(duration_s) - 1`; else `max_frames` midpoints `(i + 0.5) * duration / max_frames` plus the first keyframe when given and more than 50 ms away from every sampled time; sorted, rounded to ms.
- `sampling_params(settings: dict) -> dict` → `{"max_per_file": int, "ffmpeg_timeout_s": int}` from the settings (defaults 8 and 120).
- Settings: `frames.max_per_file: 8` (at least 1), `media.ffmpeg_timeout_s: 120` (at least 1).

- [ ] **Step 1: Write the ADR**

`docs/decisions/0007-m5-implementation-choices.md`:

```markdown
# ADR-0007: Milestone 5 implementation choices

Date: 2026-11-07 · Status: accepted

## Context

The milestone 5 task-level plan listed decisions to take before expansion: frame transport, keyframe handling, and a memory guard for animated decoding.

## Decisions

- **Frames travel over a pipe, never through temp files.** Video frames are extracted one ffmpeg call per sampled time (`-ss T -i file -frames:v 1 -f image2pipe -vcodec png -`), which seeks before decoding and is cheap for the eight-or-so frames a file gets; index-based sources (APNG) use one call with a `select` filter and the PNGs are split on the PNG signature. The task-level plan recommended a temp directory; the pipe is less code, leaves nothing behind when a child dies, and the extra process spawns are negligible next to inference.
- **ffprobe supplies duration, frame count and the first keyframe** (`-skip_frame nokey`, first packet only); when the first keyframe cannot be read, t = 0 is used.
- **Animated gif and webp pages are read one at a time with pyvips** (`page=i, n=1`), so only the sampled pages are ever decoded and no `media.max_frames_decoded` guard is needed; the task-level plan's third setting is dropped. **APNG is read with ffmpeg**, because libvips' png loader does not expose its frames.
- **Animated webp is detected by content**: any `image` entry whose decoded file reports more than one page takes the animated path (design §5.1).
- **Frame times**: gif/webp pages use the loader's per-page `delay` list (cumulative); ffmpeg-sampled frames carry the requested time or `index / fps`.
- **The thumbnail of a video is its middle sampled frame**; animated images use their first frame (design §7.2).
- **Frame images for the UI** are rendered on demand from the original (same code as fingerprinting, at 1024 px) and cached under `previews/frames/{id[:2]}/{id}-{index}.webp`; the fingerprint sink deletes them with the preview when the file is recomputed.
- **Index keys of vanished frames are retired** by `allocate_keys` (marked deleted → removed by the next `match`), so re-fingerprinting with fewer frames never leaves ghost entries.

## Consequences

- A long video costs up to `frames.max_per_file + 1` index entries (the keyframe); design §9.5's 1.5 frames average still holds for photo-heavy libraries.
- ffmpeg is a hard dependency of the compute children for video and APNG; the image and CI install it, and a missing binary marks such files `unreadable` with a clear reason rather than crashing.
```

Add `| [0007](0007-m5-implementation-choices.md) | Milestone 5 implementation choices: frames over a pipe, ffprobe for timing, pyvips pages, APNG via ffmpeg, retired frame keys | accepted |` to `docs/decisions/README.md`.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_frames_sampling.py`:

```python
from visualstore.media.frames import sample_indices, sample_times, sampling_params


def test_sample_indices_takes_all_up_to_the_cap_then_spreads_evenly():
    assert sample_indices(0, 8) == []
    assert sample_indices(1, 8) == [0]
    assert sample_indices(8, 8) == list(range(8))
    assert sample_indices(9, 8) == [0, 1, 2, 3, 5, 6, 7, 8]
    assert sample_indices(500, 8) == [0, 71, 143, 214, 285, 356, 428, 499]
    assert sample_indices(500, 1) == [0]
    spread = sample_indices(1000, 8)
    assert spread == sorted(set(spread)) and spread[0] == 0 and spread[-1] == 999


def test_sample_times_one_per_second_up_to_the_cap_then_midpoints_plus_keyframe():
    assert sample_times(0, 8, None) == [0.0]
    assert sample_times(-1, 8, 0.0) == [0.0]
    assert sample_times(1.5, 8, None) == [0.0, 1.0]
    assert sample_times(8.0, 8, None) == [float(t) for t in range(8)]
    assert sample_times(20.0, 8, None) == [1.25, 3.75, 6.25, 8.75, 11.25, 13.75, 16.25, 18.75]
    assert sample_times(20.0, 8, 0.0) == [0.0, 1.25, 3.75, 6.25, 8.75, 11.25, 13.75, 16.25, 18.75]
    assert sample_times(20.0, 8, 1.26) == [1.25, 3.75, 6.25, 8.75, 11.25, 13.75, 16.25, 18.75]  # within 50 ms
    assert sample_times(10.0, 2, 4.0) == [2.5, 4.0, 7.5]
    assert all(round(t * 1000) == t * 1000 for t in sample_times(7.777, 3, 0.3333))


def test_sampling_params_reads_the_settings_with_defaults():
    assert sampling_params({}) == {"max_per_file": 8, "ffmpeg_timeout_s": 120}
    assert sampling_params({"frames.max_per_file": 3, "media.ffmpeg_timeout_s": 9}) == {"max_per_file": 3, "ffmpeg_timeout_s": 9}
```

Append to `tests/unit/test_settings_db.py`:

```python
def test_milestone_5_defaults_and_ranges(conn):
    assert settings.get(conn, "frames.max_per_file") == 8
    assert settings.get(conn, "media.ffmpeg_timeout_s") == 120
    with pytest.raises(ValueError):
        settings.set(conn, "frames.max_per_file", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "media.ffmpeg_timeout_s", 0)
    settings.set(conn, "frames.max_per_file", 3)
    assert settings.get(conn, "frames.max_per_file") == 3
```

(`test_settings_db.py` already imports `pytest` and `settings`; check and add if not.)

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_frames_sampling.py tests/unit/test_settings_db.py -q` — Expected: FAIL (module missing; KeyError on the settings).

- [ ] **Step 4: Implement**

`visualstore/media/frames.py`:

```python
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


def sample_times(
    duration_s: float, max_frames: int, first_keyframe_s: float | None
) -> list[float]:
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
```

`visualstore/db/settings.py`: add `"frames.max_per_file": 8,` and `"media.ffmpeg_timeout_s": 120,` to `DEFAULTS`, and both keys to `_AT_LEAST_ONE`.

- [ ] **Step 5: Run the tests, lint, commit**

Run: `uv run pytest tests/unit/test_frames_sampling.py tests/unit/test_settings_db.py -q && uv run ruff check . && uv run ruff format .` — Expected: pass.

```bash
git add docs/decisions/0007-m5-implementation-choices.md docs/decisions/README.md visualstore/media/frames.py visualstore/db/settings.py tests/unit/test_frames_sampling.py tests/unit/test_settings_db.py
git commit -m "feat: frame sampling rules and settings; ADR-0007 for milestone 5 choices

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Video probing and frame extraction with ffmpeg

**Files:**
- Create: `visualstore/media/video.py`, `tests/fixtures/media.py`, `tests/unit/test_video.py`
- Modify: `visualstore/media/decode.py`

**Interfaces:**
- `decode.Decoded` gains `pages: int = 1`; `load_image` sets it from the loader's `n-pages` when present.
- `decode.normalise(img) -> pyvips.Image` — the autorot/flatten/srgb/3-band/uchar/`copy_memory` chain shared by `load_image`, pages and PNG frames (raises `Unreadable` on a pyvips error).
- `decode.image_from_png(data: bytes, max_pixels: int) -> Decoded` — decodes PNG bytes from ffmpeg.
- `video.Probe(duration_s: float, fps: float, nb_frames: int | None, first_keyframe_s: float | None)`.
- `video.probe(path: str, timeout_s: int) -> Probe` — raises `NotMedia` when ffprobe reports no video stream (or cannot parse the file), `Unreadable` on timeout or a missing binary.
- `video.frames_at_times(path, times_s: list[float], timeout_s, max_pixels) -> list[Frame]` — one ffmpeg call per time; times that yield no image are skipped; `index` is the position in the returned list, `time_ms = round(t * 1000)`; raises `Unreadable("no decodable frames")` when nothing decodes, `Unreadable("ffmpeg timed out after …")` on timeout.
- `video.frames_at_indices(path, indices: list[int], timeout_s, max_pixels, fps) -> list[Frame]` — one call with `select`; `time_ms = round(index / fps * 1000)`.
- `video.frame_png(path, time_s, timeout_s) -> bytes` — raw PNG of one frame (used by the frames API through `image_from_png`); empty output raises `Unreadable`.
- Fixture helpers (`tests/fixtures/media.py`): `require_ffmpeg()` (module-level skip like `require_pyvips`), `make_clip(path, seconds, size="160x120", rate=10, colour=None)` (testsrc, or a solid `color=c=…` source; `-movflags +faststart`, `yuv420p`), `make_gif(path, frames, rate=5, size="64x48")`, `make_apng(path, frames, rate=5, size="32x24")`, `make_webp_anim(path, frames, rate=5, size="64x48")`, `truncate_file(path, keep_fraction)`.

- [ ] **Step 1: Write the fixture helpers and the failing tests**

`tests/fixtures/media.py`:

```python
"""Synthetic media generated with ffmpeg at test time (skipped where ffmpeg is absent)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def require_ffmpeg():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not available", allow_module_level=True)


def _run(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


def make_clip(path: Path, seconds: float, size: str = "160x120", rate: int = 10, colour: str | None = None) -> Path:
    source = f"color=c={colour}:size={size}:rate={rate}" if colour else f"testsrc=size={size}:rate={rate}"
    _run(["-f", "lavfi", "-i", source, "-t", str(seconds), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)])
    return path


def make_gif(path: Path, frames: int, rate: int = 5, size: str = "64x48") -> Path:
    _run(["-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}", "-frames:v", str(frames), str(path)])
    return path


def make_apng(path: Path, frames: int, rate: int = 5, size: str = "32x24") -> Path:
    _run(["-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}", "-frames:v", str(frames), "-f", "apng", str(path)])
    return path


def make_webp_anim(path: Path, frames: int, rate: int = 5, size: str = "64x48") -> Path:
    _run(["-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}", "-frames:v", str(frames), "-c:v", "libwebp_anim", "-loop", "0", str(path)])
    return path


def truncate_file(path: Path, keep_fraction: float) -> Path:
    data = path.read_bytes()
    path.write_bytes(data[: int(len(data) * keep_fraction)])
    return path
```

`tests/unit/test_video.py`:

```python
import pytest

from tests.fixtures.media import make_clip, require_ffmpeg, truncate_file
from tests.fixtures.vips import require_pyvips

require_ffmpeg()
pyvips = require_pyvips()

from visualstore.media.errors import NotMedia, Unreadable  # noqa: E402
from visualstore.media.frames import sample_times  # noqa: E402
from visualstore.media.video import frame_png, frames_at_indices, frames_at_times, probe  # noqa: E402


def test_probe_reads_duration_fps_frames_and_first_keyframe(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    p = probe(str(clip), 30)
    assert abs(p.duration_s - 3.0) < 0.2 and p.fps == 10.0 and p.nb_frames == 30
    assert p.first_keyframe_s == 0.0
    (tmp_path / "text.mp4").write_text("not a video")
    with pytest.raises(NotMedia):
        probe(str(tmp_path / "text.mp4"), 30)


def test_frames_at_times_returns_one_frame_per_time_in_order(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    times = sample_times(probe(str(clip), 30).duration_s, 8, 0.0)
    frames = frames_at_times(str(clip), times, 30, 100_000_000)
    assert [f.time_ms for f in frames] == [0, 1000, 2000]
    assert [f.index for f in frames] == [0, 1, 2]
    assert all((f.image.width, f.image.height, f.image.bands) == (160, 120, 3) for f in frames)
    long_clip = make_clip(tmp_path / "long.mp4", 20, size="64x48")
    p = probe(str(long_clip), 30)
    frames = frames_at_times(str(long_clip), sample_times(p.duration_s, 8, p.first_keyframe_s), 30, 100_000_000)
    assert len(frames) == 9 and frames[0].time_ms == 0 and frames[-1].time_ms == 18750


def test_frames_at_indices_selects_exact_frames(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    frames = frames_at_indices(str(clip), [0, 10, 29], 30, 100_000_000, fps=10.0)
    assert [f.time_ms for f in frames] == [0, 1000, 2900] and len(frames) == 3


def test_truncated_video_yields_the_decodable_frames_and_no_temp_files(tmp_path, monkeypatch):
    """Review Focus 1."""
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    clip = make_clip(tmp_path / "c.mp4", 6)
    truncate_file(clip, 0.4)
    p = probe(str(clip), 30)
    frames = frames_at_times(str(clip), sample_times(p.duration_s, 8, p.first_keyframe_s), 30, 100_000_000)
    assert 1 <= len(frames) < 6 and frames[0].time_ms == 0
    assert list((tmp_path / "tmp").iterdir()) == []
    truncate_file(clip, 0.01)
    with pytest.raises((Unreadable, NotMedia)):
        frames_at_times(str(clip), [0.0, 1.0], 30, 100_000_000)


def test_timeout_marks_the_file_unreadable_not_hanging(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    with pytest.raises(Unreadable, match="timed out"):
        frames_at_times(str(clip), [0.0], 0.001, 100_000_000)  # type: ignore[arg-type]


def test_frame_png_returns_a_png(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    data = frame_png(str(clip), 1.0, 30)
    assert data.startswith(b"\x89PNG") and pyvips.Image.new_from_buffer(data, "").width == 160
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker run --rm -v "$PWD/visualstore:/app/visualstore:ro" -v "$PWD/tests:/app/tests:ro" visualstore:test python -m pytest -p no:cacheprovider tests/unit/test_video.py -q` — Expected: FAIL (`visualstore.media.video` missing). On the host the file skips.

- [ ] **Step 3: Implement**

In `visualstore/media/decode.py`: add `pages: int = 1` to `Decoded`; extract the normalisation chain:

```python
def normalise(img: pyvips.Image) -> pyvips.Image:
    """EXIF rotation, alpha flattened on white, sRGB, three uchar bands, decoded in memory."""
    try:
        img = img.autorot()
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        if img.interpretation != "srgb" or img.bands != 3:
            img = img.colourspace("srgb")
        if img.bands > 3:
            img = img.extract_band(0, n=3)
        if img.format != "uchar":  # 16-bit / float sources: bound the buffer to w*h*3 bytes
            img = img.cast("uchar")
        return img.copy_memory()  # decode now so truncated data fails here, not later
    except pyvips.Error as exc:
        raise Unreadable(str(exc)) from exc
```

`load_image` becomes: open as before, check `max_pixels`, then `pages = img.get("n-pages") if img.get_typeof("n-pages") else 1`, `img = normalise(img)`, return `Decoded(image=img, width=img.width, height=img.height, pages=int(pages))`. Add:

```python
def image_from_png(data: bytes, max_pixels: int) -> Decoded:
    """A frame that ffmpeg wrote as PNG."""
    try:
        img = pyvips.Image.new_from_buffer(data, "", access="sequential", fail_on="truncated")
    except pyvips.Error as exc:
        raise Unreadable(str(exc)) from exc
    if img.width * img.height > max_pixels:
        raise Unreadable(f"{img.width}x{img.height} exceeds the limit of {max_pixels} pixels")
    img = normalise(img)
    return Decoded(image=img, width=img.width, height=img.height)
```

`visualstore/media/video.py`:

```python
"""Video probing and frame extraction with ffmpeg/ffprobe (design §7.2, §7.3, ADR-0007).
Frames come back over a pipe as PNG; no temp files."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction

from visualstore.media.decode import image_from_png
from visualstore.media.errors import NotMedia, Unreadable
from visualstore.media.frames import Frame

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COMMON = ["-nostdin", "-hide_banner", "-loglevel", "error"]


@dataclass(frozen=True)
class Probe:
    duration_s: float
    fps: float
    nb_frames: int | None
    first_keyframe_s: float | None


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
            *_COMMON,
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=nb_frames,r_frame_rate,avg_frame_rate,codec_type",
            "-of",
            "json",
            path,
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
    return Probe(duration, fps or 25.0, nb_frames, _first_keyframe(path, timeout_s))


def _first_keyframe(path: str, timeout_s: float) -> float | None:
    try:
        out = _run(
            [
                "ffprobe",
                *_COMMON,
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
                path,
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
            *_COMMON,
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            path,
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
            *_COMMON,
            "-ss",
            f"{time_s:.3f}",
            "-i",
            path,
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
    select = "+".join(f"eq(n\\,{i})" for i in indices)
    data = _run(
        [
            "ffmpeg",
            *_COMMON,
            "-i",
            path,
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
```

- [ ] **Step 4: Run the tests**

Run the Docker command from Step 2 plus `tests/unit/test_decode.py` — Expected: pass. Then on the host `uv run pytest -q` (video tests skip) and `uv run ruff check . && uv run ruff format .`.

- [ ] **Step 5: Commit**

```bash
git add visualstore/media/video.py visualstore/media/decode.py tests/fixtures/media.py tests/unit/test_video.py
git commit -m "feat: video probing and frame extraction over an ffmpeg pipe

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Animated images

**Files:**
- Create: `visualstore/media/animated.py`, `tests/unit/test_animated.py`

**Interfaces:**
- `animated.backend(path: str) -> str` — `"ffmpeg"` for a `.apng` suffix (case-insensitive), `"vips"` otherwise.
- `animated.page_count(path, max_pixels) -> int` — pyvips page count (1 when the loader has no pages); raises `NotMedia` / `Unreadable` like `load_image`.
- `animated.page_times_ms(path) -> list[int]` — cumulative start times from the loader's `delay` list (ms per page); `[0]` when absent; monotone (zero delays are raised to 1 ms steps).
- `animated.load_pages(path, indices: list[int], max_pixels) -> list[Frame]` — one `new_from_source(..., page=i, n=1)` per index; `Frame.index` is the position, `time_ms` from `page_times_ms`.
- `animated.frames(path, max_frames, timeout_s, max_pixels) -> list[Frame]` — the whole sampled set for either backend (ffmpeg: `count_frames` + `frames_at_indices` with the probed fps).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_animated.py`:

```python
import pytest

from tests.fixtures.images import png_bytes
from tests.fixtures.media import make_apng, make_gif, make_webp_anim, require_ffmpeg
from tests.fixtures.vips import require_pyvips

require_ffmpeg()
pyvips = require_pyvips()

from visualstore.media.animated import backend, frames, load_pages, page_count, page_times_ms  # noqa: E402
from visualstore.media.frames import sample_indices  # noqa: E402

MAX = 100_000_000


def test_backend_by_suffix():
    assert backend("/x/a.gif") == "vips" and backend("/x/a.webp") == "vips"
    assert backend("/x/a.APNG") == "ffmpeg" and backend("/x/a.png") == "vips"


def test_page_count_and_times_for_gif_webp_and_a_still(tmp_path):
    gif = make_gif(tmp_path / "a.gif", 10)
    assert page_count(str(gif), MAX) == 10
    assert page_times_ms(str(gif)) == [i * 200 for i in range(10)]
    webp = make_webp_anim(tmp_path / "a.webp", 4)
    assert page_count(str(webp), MAX) == 4 and page_times_ms(str(webp))[:2] == [0, 200]
    still = tmp_path / "s.png"
    still.write_bytes(png_bytes(20, 10))
    assert page_count(str(still), MAX) == 1 and page_times_ms(str(still)) == [0]


def test_load_pages_returns_the_requested_pages_as_single_frames(tmp_path):
    gif = make_gif(tmp_path / "a.gif", 10)
    got = load_pages(str(gif), [0, 4, 9], MAX)
    assert [(f.index, f.time_ms) for f in got] == [(0, 0), (1, 800), (2, 1800)]
    assert all((f.image.width, f.image.height, f.image.bands) == (64, 48, 3) for f in got)


def test_frames_follow_the_sampling_rules_for_each_backend(tmp_path):
    """Review Focus 2: 1-frame gif → 1, 10-frame gif → 8, 500-frame apng → 8, increasing times."""
    one = make_gif(tmp_path / "one.gif", 1)
    assert [f.time_ms for f in frames(str(one), 8, 60, MAX)] == [0]
    ten = make_gif(tmp_path / "ten.gif", 10)
    got = frames(str(ten), 8, 60, MAX)
    assert len(got) == 8 and [f.index for f in got] == list(range(8))
    assert [f.time_ms for f in got] == [i * 200 for i in sample_indices(10, 8)]
    apng = make_apng(tmp_path / "big.apng", 500)
    got = frames(str(apng), 8, 120, MAX)
    times = [f.time_ms for f in got]
    assert len(got) == 8 and times == sorted(times) and len(set(times)) == 8 and times[0] == 0
    assert times[-1] == round(499 / 5 * 1000)
    assert [f.time_ms for f in frames(str(ten), 3, 60, MAX)] == [0, 800, 1800]
```

- [ ] **Step 2: Run them to verify they fail**

Run: Docker pytest on `tests/unit/test_animated.py` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

`visualstore/media/animated.py`:

```python
"""Animated images: gif/webp pages with pyvips, APNG through ffmpeg (design §7.2, ADR-0007)."""

from __future__ import annotations

from pathlib import PurePath

import pyvips

from visualstore.media.decode import _NOT_MEDIA_MARKERS, normalise
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
        time_ms = times[index] if index < len(times) else index * 100
        frames.append(Frame(position, time_ms, normalise(img)))
    return frames


def frames(path: str, max_frames: int, timeout_s: float, max_pixels: int) -> list[Frame]:
    """The sampled frames of an animated image through the right backend."""
    if backend(path) == "ffmpeg":
        from visualstore.media.video import count_frames, frames_at_indices, probe

        count = count_frames(path, timeout_s)
        fps = probe(path, timeout_s).fps
        return frames_at_indices(path, sample_indices(count, max_frames), timeout_s, max_pixels, fps)
    count = page_count(path, max_pixels)
    return load_pages(path, sample_indices(count, max_frames), max_pixels)
```

- [ ] **Step 4: Run the tests, lint, commit**

Run: Docker pytest on `tests/unit/test_animated.py tests/unit/test_video.py tests/unit/test_decode.py` — Expected: pass. `uv run ruff check . && uv run ruff format .`.

```bash
git add visualstore/media/animated.py tests/unit/test_animated.py
git commit -m "feat: animated image frames through pyvips pages and ffmpeg

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Fingerprint branches, batch embedding, retired frame keys

**Files:**
- Modify: `visualstore/jobs/compute/handlers.py`, `visualstore/db/fingerprints.py`, `visualstore/jobs/io/scan.py`, `visualstore/jobs/io/model_switch.py`, `visualstore/jobs/io/actions.py`, `tests/unit/test_db_fingerprints.py`, `tests/unit/test_fingerprint_vectors.py`, `tests/integration/test_scan_job.py`

**Interfaces:**
- Fingerprint job params gain `"frames": {"max_per_file", "ffmpeg_timeout_s"}` (from `sampling_params(ctx.settings)`) in the scan, model-switch and restore enqueues; the handler defaults to 8 / 120 when absent.
- Handler: `_embed_frames(frames: list[Frame], model, thumb_index) -> (frames_out, thumb)` — one `session.embed` call over the batch; `frames_out = [{"index", "time_ms", "vector"}]`, thumbnail from `frames[thumb_index]`.
- Branches: `image` → `load_image`; if `decoded.pages > 1` → `animated.frames(...)` (thumbnail index 0); `animated` → `animated.frames(...)` (thumbnail 0); `video` → `probe` + `sample_times` + `frames_at_times` (thumbnail `len // 2`); `other` → sha1 only.
- `fp.allocate_keys(vconn, file_id, model_id, frame_indices)` marks keys of this file/model whose `frame_index` is not in `frame_indices` as `deleted = 1` (Review Focus 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_db_fingerprints.py` (uses the existing `vconn` fixture):

```python
def test_allocate_keys_retires_frames_that_vanished(vconn):
    """Review Focus 4: re-fingerprinting with fewer frames replaces, never appends."""
    fid, model = b"v" * 16, "m"
    fingerprints.replace_frames(vconn, fid, model, [(i, i * 100, b"\x00" * 16) for i in range(4)])
    first = fingerprints.allocate_keys(vconn, fid, model, [0, 1, 2, 3])
    assert len(first) == 4 and fingerprints.pending_removals(vconn, model) == []
    fingerprints.replace_frames(vconn, fid, model, [(i, i * 100, b"\x01" * 16) for i in range(2)])
    second = fingerprints.allocate_keys(vconn, fid, model, [0, 1])
    assert second == first[:2]
    assert fingerprints.pending_removals(vconn, model) == first[2:]
    assert set(fingerprints.resolve_keys(vconn, first)) == set(first[:2])
    assert len(fingerprints.frames_of_file(vconn, fid, model)) == 2
```

(Check the module's import name for `fingerprints`; the file imports `from visualstore.db import fingerprints` or similar — match it.)

Append to `tests/unit/test_fingerprint_vectors.py` (which already has `world`, `entry`, `job_params`, `fingerprint`, `sink_fingerprint`, `make_sink_ctx`; add `from tests.fixtures.media import make_clip, make_gif, require_ffmpeg` and call `require_ffmpeg()` after `pyvips = require_pyvips()`):

```python
def test_handler_samples_frames_of_gif_webp_by_content_and_video(world):
    reg, conn, lib, model_dir, spec, add = world
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    make_clip(lib / "clip.mp4", 3)
    clip = add("clip.mp4", (lib / "clip.mp4").read_bytes(), media="mp4")
    from tests.fixtures.media import make_webp_anim

    make_webp_anim(lib / "moving.webp", 4)
    webp = add("moving.webp", (lib / "moving.webp").read_bytes(), media="webp")  # extension says image
    result = fingerprint(
        {
            "files": [
                entry(gif, lib, "anim.gif", "animated"),
                entry(clip, lib, "clip.mp4", "video"),
                entry(webp, lib, "moving.webp", "image"),
            ],
            "model": job_params(spec, model_dir),
            "max_pixels": 10_000_000,
            "frames": {"max_per_file": 3, "ffmpeg_timeout_s": 60},
        },
        lambda p: None,
    )
    by_id = {e["id"]: e for e in result["files"]}
    g, c, w = by_id[id_to_str(gif)], by_id[id_to_str(clip)], by_id[id_to_str(webp)]
    assert [f["time_ms"] for f in g["frames"]] == [0, 800, 1800] and g["state"] == "fingerprinted"
    assert [f["time_ms"] for f in c["frames"]] == [0, 1000, 2000] and c["thumbnail"]
    assert [f["index"] for f in w["frames"]] == [0, 1, 2] and w["frames"][1]["time_ms"] == 200
    assert all(len(base64.b64decode(f["vector"])) == 16 for f in g["frames"] + c["frames"])
    thumb = pyvips.Image.new_from_buffer(base64.b64decode(c["thumbnail"]), "")
    assert thumb.width == 160  # middle frame, 256 px cap not reached


def test_sink_replaces_frames_and_retires_keys_when_fewer_frames_come_back(world):
    """Review Focus 4 through the sink."""
    reg, conn, lib, model_dir, spec, add = world
    vconn = reg.vectors_conn("a")
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    params = {"model": job_params(spec, model_dir), "max_pixels": 10_000_000}

    def run(max_frames):
        result = fingerprint(
            {**params, "files": [entry(gif, lib, "anim.gif", "animated")], "frames": {"max_per_file": max_frames, "ffmpeg_timeout_s": 60}},
            lambda p: None,
        )
        sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))

    run(8)
    assert len(fingerprints.frames_of_file(vconn, gif, spec.id)) == 8
    keys = fingerprints.allocate_keys(vconn, gif, spec.id, list(range(8)))
    run(2)
    assert len(fingerprints.frames_of_file(vconn, gif, spec.id)) == 2
    assert fingerprints.pending_removals(vconn, spec.id) == keys[2:]
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    assert json.loads(job["params"])["remove"] == keys[2:]
```

(The second `run` queues `match` only when the index exists; the world fixture of this file leaves no index, so the first `run` queues `rebuild_index` and the second `run` — with the rebuild still queued — queues nothing. Adjust the test to what the fixture gives: after `run(8)` mark the index usable by `fingerprints.set_meta(vconn, "model_id", spec.id)` and `reg.paths("a").index.write_bytes(b"")` is **not** enough because `index_ok` also needs the file — write an empty `VectorIndex.empty(8).save(reg.paths("a").index)` first, before `run(8)`; then both runs queue `match` and the second one's `remove` is `keys[2:]`.)

Append to `tests/integration/test_scan_job.py`:

```python
def test_fingerprint_jobs_carry_frame_sampling_params(world):
    reg, conn, folder, lib = world
    settings_db.set(conn, "frames.max_per_file", 5)
    run_scan(ctx_for(reg, folder))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "fingerprint"]
    assert json.loads(job["params"])["frames"] == {"max_per_file": 5, "ffmpeg_timeout_s": 120}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/test_db_fingerprints.py tests/integration/test_scan_job.py -q` — Expected: 2 failures. Docker pytest on `tests/unit/test_fingerprint_vectors.py` — Expected: 2 failures (no frames / wrong counts).

- [ ] **Step 3: Implement**

`visualstore/db/fingerprints.py`, in `allocate_keys` after the two `executemany` calls and inside the same `with vconn:` block:

```python
        marks = ",".join("?" * len(frame_indices)) or "NULL"
        vconn.execute(
            "UPDATE index_key SET deleted = 1 WHERE file_id = ? AND model_id = ?"
            f" AND frame_index NOT IN ({marks})",
            (file_id, model_id, *frame_indices),
        )
```

`visualstore/jobs/compute/handlers.py`: replace `_embed_image` with

```python
def _embed_frames(frames: list, model: dict, thumb_index: int) -> tuple[list[dict], str]:
    from visualstore.media.decode import Decoded, thumbnail_webp, to_model_input

    session = _session(model)
    size = int(model["input_size"])
    batch = np.concatenate(
        [
            to_model_input(Decoded(f.image, f.image.width, f.image.height), size, model["mean"], model["std"])
            for f in frames
        ]
    )
    vectors = session.embed(batch)
    out = [
        {
            "index": f.index,
            "time_ms": f.time_ms,
            "vector": base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii"),
        }
        for f, v in zip(frames, vectors, strict=True)
    ]
    t = frames[thumb_index]
    thumb = thumbnail_webp(Decoded(t.image, t.image.width, t.image.height), 256)
    return out, base64.b64encode(thumb).decode("ascii")


def _frames_for(path: str, media_type: str, max_pixels: int, sampling: dict) -> tuple[list, int]:
    """The sampled frames of a file and which one becomes the thumbnail."""
    from visualstore.media import animated, video
    from visualstore.media.decode import load_image
    from visualstore.media.frames import Frame, sample_times

    max_frames = int(sampling.get("max_per_file", 8))
    timeout = float(sampling.get("ffmpeg_timeout_s", 120))
    if media_type == "video":
        info = video.probe(path, timeout)
        times = sample_times(info.duration_s, max_frames, info.first_keyframe_s)
        frames = video.frames_at_times(path, times, timeout, max_pixels)
        return frames, len(frames) // 2
    if media_type == "animated":
        return animated.frames(path, max_frames, timeout, max_pixels), 0
    decoded = load_image(path, max_pixels)
    if decoded.pages > 1:  # an animated webp with an image extension (design §5.1)
        return animated.frames(path, max_frames, timeout, max_pixels), 0
    return [Frame(0, 0, decoded.image)], 0
```

and in `fingerprint`: `sampling = params.get("frames", {})`; the branch condition becomes `if model and entry.get("media_type") in ("image", "animated", "video"):` with body

```python
                try:
                    frames, thumb_index = _frames_for(path, entry["media_type"], max_pixels, sampling)
                    frames_out, thumb = _embed_frames(frames, model, thumb_index)
                    record.update({"frames": frames_out, "thumbnail": thumb, "model_id": model["id"]})
```

(the `except` clauses stay as they are). Update the docstring: "SHA-1 for every file; vectors and a thumbnail for images, animated images and video frames (design §7.2, §7.3)".

`visualstore/jobs/io/scan.py` `_enqueue_fingerprints` params: add `"frames": sampling_params(ctx.settings),` (import `from visualstore.media.frames import sampling_params`). Same in `model_switch.py` (its fingerprint params) and in `actions.py::_refingerprint`.

- [ ] **Step 4: Run the tests, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format .` and Docker pytest on `tests/unit/test_fingerprint_vectors.py tests/unit/test_video.py tests/unit/test_animated.py tests/unit/test_decode.py tests/unit/test_match.py` — Expected: pass.

```bash
git add visualstore/jobs/compute/handlers.py visualstore/db/fingerprints.py visualstore/jobs/io/scan.py visualstore/jobs/io/model_switch.py visualstore/jobs/io/actions.py tests/unit/test_db_fingerprints.py tests/unit/test_fingerprint_vectors.py tests/integration/test_scan_job.py
git commit -m "feat: fingerprint animated images and videos frame by frame; retire keys of vanished frames

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Still-versus-video matching, end to end

**Files:**
- Create: `tests/integration/test_still_vs_video.py`

**Interfaces:** none new; this task proves Review Focus 3 through `fingerprint` → `sink_fingerprint` → `match` → `sink_match` with the tiny model.

- [ ] **Step 1: Write the test**

`tests/integration/test_still_vs_video.py`:

```python
import json

from tests.fixtures.media import make_clip, require_ffmpeg
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.fixtures.vips import require_pyvips
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.compute.handlers import fingerprint, match
from visualstore.jobs.results import sink_fingerprint, sink_match
from visualstore.models.registry import job_params
from visualstore.profiles import ProfileRegistry

require_ffmpeg()
pyvips = require_pyvips()


def test_a_still_cut_from_a_video_lands_in_the_near_duplicate_band(tmp_path):
    """Review Focus 3: one vector space for stills and frames (design §7.3)."""
    import subprocess

    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    settings_db.set(conn, "model.id", spec.id)
    VectorIndex.empty(8).save(reg.paths("a").index)
    from visualstore.db import fingerprints as fp

    fp.set_meta(vconn, "model_id", spec.id)
    make_clip(lib / "clip.mp4", 3, colour="0x336699")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0", "-i", str(lib / "clip.mp4"), "-frames:v", "1", str(lib / "still.png")], check=True)
    (lib / "other.png").write_bytes(__import__("tests.fixtures.images", fromlist=["png_bytes"]).png_bytes(64, 64, rgb=(250, 20, 20)))

    def add(name, media):
        st = (lib / name).stat()
        return files.insert_file(conn, folder_id=folder, directory="", name=name, extension_id=ext[media]["id"], size=st.st_size, mtime=st.st_mtime_ns)

    clip, still, other = add("clip.mp4", "mp4"), add("still.png", "png"), add("other.png", "png")
    params = {"model": job_params(spec, model_dir), "max_pixels": 10_000_000, "frames": {"max_per_file": 8, "ffmpeg_timeout_s": 60}}
    entries = [
        {"id": id_to_str(clip), "path": str(lib / "clip.mp4"), "media_type": "video"},
        {"id": id_to_str(still), "path": str(lib / "still.png"), "media_type": "image"},
        {"id": id_to_str(other), "path": str(lib / "other.png"), "media_type": "image"},
    ]
    result = fingerprint({**params, "files": entries}, lambda p: None)
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    match_params = json.loads(job["params"])
    hits = match(match_params, lambda p: None)
    sink_match(make_sink_ctx(reg, "a", match_params, hits, model_dir))
    [pair] = [p for p in pairs.pairs_of_file(conn, still) if bytes(p["file_a"]) == clip or bytes(p["file_b"]) == clip]
    assert pair["distance"] < 0.15, pair["distance"]
    assert pair["model_id"] == spec.id and pair["source"] == "vector"
    reg.close()
```

- [ ] **Step 2: Run it**

Run: Docker pytest on `tests/integration/test_still_vs_video.py` — Expected: 1 passed (if it fails on the distance, the vector pipeline is wrong; debug, do not loosen the threshold).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_still_vs_video.py
git commit -m "test: a still cut from a video matches the video in the near-duplicate band

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Frames API and member media type

**Files:**
- Modify: `visualstore/api/files.py`, `visualstore/db/groups.py`, `visualstore/jobs/results.py`, `tests/integration/test_api_preview.py`, `tests/integration/test_api_groups.py`, `web/src/types.ts`

**Interfaces:**
- `GET /api/p/{name}/files/{id}/frames` → `{"media_type": str, "frames": [{"index": int, "time_ms": int}]}` from `vectors.db` for the active model (empty list when not fingerprinted yet).
- `GET /api/p/{name}/files/{id}/frame/{index}` → `image/webp` (1024 px) of that frame, cached at `previews/frames/{id[:2]}/{id}-{index}.webp`; 404 `"No such frame."` when the index is not in the frame list, `"The file is not on disk."`, or `"Cannot decode the file: …"`.
- Rendering: `video` and `.apng` → `video.frame_png(path, time_ms / 1000, timeout)` → `image_from_png` → webp; other animated → `animated.load_pages(path, [source page], …)` where the source page is recovered as `sample_indices(page_count, max_per_file)[index]`; `image` with one frame → same bytes as the preview.
- `_cached_render(cache: Path, render: Callable[[], bytes]) -> Path` — the per-file lock + semaphore logic from `file_preview`, shared.
- Group members gain `media_type` (`_MEMBER_SQL` joins `extension`); `GroupMember.media_type` in `types.ts`; new types `FrameInfo {index, time_ms}`, `FramesResponse {media_type, frames}`.
- `sink_fingerprint` also deletes `previews/frames/{id[:2]}/{id}-*.webp` when it writes a thumbnail.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_api_preview.py` (add `from tests.fixtures.media import make_clip, make_gif, require_ffmpeg` and `require_ffmpeg()` after `pyvips = require_pyvips()`; the file's `seed` helper returns `reg, lib, add`):

```python
def fingerprinted(reg, lib, fid, name, media_type):
    """Run the real handler + sink for one file so vectors.db has its frames."""
    from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
    from visualstore.db import settings as settings_db
    from visualstore.jobs.compute.handlers import fingerprint
    from visualstore.models.registry import job_params

    conn = reg.conn("a")
    model_dir = reg.data_dir / "models"
    model_dir.mkdir(exist_ok=True)
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    settings_db.set(conn, "model.id", spec.id)
    params = {"model": job_params(spec, model_dir), "max_pixels": 10_000_000, "frames": {"max_per_file": 8, "ffmpeg_timeout_s": 60}}
    result = fingerprint({**params, "files": [{"id": id_to_str(fid), "path": str(lib / name), "media_type": media_type}]}, lambda p: None)
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))


def test_frames_listing_and_frame_images(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    make_clip(lib / "clip.mp4", 3)
    clip = add("clip.mp4", (lib / "clip.mp4").read_bytes(), media="mp4")
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    still = add("still.png", png_bytes(30, 20))
    assert client.get(f"/api/p/a/files/{id_to_str(clip)}/frames").json() == {"media_type": "video", "frames": []}
    for fid, name, media in ((clip, "clip.mp4", "video"), (gif, "anim.gif", "animated"), (still, "still.png", "image")):
        fingerprinted(reg, lib, fid, name, media)
    listed = client.get(f"/api/p/a/files/{id_to_str(clip)}/frames").json()
    assert listed["media_type"] == "video" and [f["time_ms"] for f in listed["frames"]] == [0, 1000, 2000]
    gif_frames = client.get(f"/api/p/a/files/{id_to_str(gif)}/frames").json()["frames"]
    assert [f["time_ms"] for f in gif_frames] == [i * 200 for i in (0, 1, 3, 4, 5, 6, 8, 9)]
    assert client.get(f"/api/p/a/files/{id_to_str(still)}/frames").json()["frames"] == [{"index": 0, "time_ms": 0}]
    got = client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/1")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
    assert pyvips.Image.new_from_buffer(got.content, "").width == 160
    cache = reg.paths("a").previews / "frames" / id_to_str(clip)[:2] / f"{id_to_str(clip)}-1.webp"
    assert cache.is_file()
    page = client.get(f"/api/p/a/files/{id_to_str(gif)}/frame/2")  # source page 3
    assert page.status_code == 200 and pyvips.Image.new_from_buffer(page.content, "").width == 64
    assert client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/9").json()["detail"] == "No such frame."
    (lib / "clip.mp4").unlink()
    assert client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/2").json()["detail"] == "The file is not on disk."
    # a new fingerprint drops the frame cache with the preview
    sink_fingerprint(make_sink_ctx(reg, "a", {}, {"files": [{"id": id_to_str(clip), "sha1": "00" * 20, "state": "fingerprinted", "thumbnail": "AAAA"}]}))
    assert not cache.exists()
```

Append to `tests/integration/test_api_groups.py`:

```python
def test_group_members_carry_their_media_type(client, seeded):
    detail = client.get(f"/api/p/a/groups/{id_to_str(seeded['gid'])}").json()
    assert {m["media_type"] for m in detail["group"]["members"]} == {"image"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: Docker pytest on `tests/integration/test_api_preview.py` — Expected: FAIL (404 on `/frames`). `uv run pytest tests/integration/test_api_groups.py -q -k media_type` — Expected: FAIL (KeyError).

- [ ] **Step 3: Implement**

`visualstore/db/groups.py` `_MEMBER_SQL`: add `" e.media_type,"` to the select list and `" JOIN extension e ON e.id = f.extension_id"` after the folder join.

`visualstore/api/files.py`: refactor the preview body into

```python
async def _cached_render(cache: Path, render: Callable[[], bytes]) -> Path:
    """Render once per file (per-file lock), at most two renders at a time, atomic cache write."""
    lock = _RENDERING.setdefault(cache, asyncio.Lock())
    try:
        async with lock:
            if cache.is_file():
                return cache
            async with _RENDER_SLOTS:
                try:
                    data = await asyncio.to_thread(render)
                except (NotMedia, Unreadable) as exc:
                    raise HTTPException(
                        status_code=404, detail=f"Cannot decode the file: {exc}"
                    ) from None
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(cache)
    finally:
        if not lock.locked() and _RENDERING.get(cache) is lock:
            _RENDERING.pop(cache, None)
    return cache
```

(`from collections.abc import Callable`), `file_preview` calls `await _cached_render(cache, lambda: preview_webp(str(path), PREVIEW_PX, max_pixels))`. Add:

```python
def _frames_of(state, name: str, conn, fid: bytes) -> list:
    from visualstore.db import fingerprints as fp

    vconn = state.registry.vectors_conn(name)
    return fp.frames_of_file(vconn, fid, settings_db.get(conn, "model.id"))


@router.get("/{file_id}/frames")
async def file_frames(file_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    state = get_state(request)
    conn = state.registry.conn(name)
    fid = parse_id(file_id)
    row, _ = _file_and_folder(conn, fid)
    frames = _frames_of(state, name, conn, fid)
    return {
        "media_type": _media_type(conn, bytes(row["extension_id"])),
        "frames": [{"index": r["frame_index"], "time_ms": r["frame_time_ms"]} for r in frames],
    }


def _render_frame(path: str, media_type: str, index: int, time_ms: int, settings: dict) -> bytes:
    from visualstore.media import animated, video
    from visualstore.media.decode import image_from_png, load_image, preview_webp, thumbnail_webp
    from visualstore.media.frames import sample_indices

    max_pixels = int(settings["media.max_pixels"])
    timeout = float(settings["media.ffmpeg_timeout_s"])
    if media_type == "video" or animated.backend(path) == "ffmpeg":
        decoded = image_from_png(video.frame_png(path, time_ms / 1000, timeout), max_pixels)
        return thumbnail_webp(decoded, PREVIEW_PX)
    pages = animated.page_count(path, max_pixels)
    if pages <= 1:
        return preview_webp(path, PREVIEW_PX, max_pixels)
    source_pages = sample_indices(pages, int(settings["frames.max_per_file"]))
    if index >= len(source_pages):
        raise Unreadable(f"frame {index} is beyond the sampled pages")
    [frame] = animated.load_pages(path, [source_pages[index]], max_pixels)
    from visualstore.media.decode import Decoded

    return thumbnail_webp(Decoded(frame.image, frame.image.width, frame.image.height), PREVIEW_PX)


@router.get("/{file_id}/frame/{index}")
async def file_frame(
    file_id: str, index: int, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    state = get_state(request)
    conn = state.registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    frames = {r["frame_index"]: r["frame_time_ms"] for r in _frames_of(state, name, conn, fid)}
    if index not in frames:
        raise HTTPException(status_code=404, detail="No such frame.")
    text = id_to_str(fid)
    cache = state.registry.paths(name).previews / "frames" / text[:2] / f"{text}-{index}.webp"
    if cache.is_file():
        return FileResponse(cache, media_type="image/webp")
    path = Path(folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file is not on disk.")
    media_type = _media_type(conn, bytes(row["extension_id"]))
    settings = settings_db.all(conn)
    await _cached_render(
        cache, lambda: _render_frame(str(path), media_type, index, frames[index], settings)
    )
    return FileResponse(cache, media_type="image/webp")
```

(`thumbnail_webp(decoded, longest)` already exists and never upscales.) Note the route order: `/{file_id}/frame/{index}` and `/{file_id}/frames` are distinct paths; no conflict with `/{file_id}`.

`visualstore/jobs/results.py` where the preview is unlinked:

```python
            (ctx.paths.previews / text[:2] / f"{text}.webp").unlink(missing_ok=True)
            for stale in (ctx.paths.previews / "frames" / text[:2]).glob(f"{text}-*.webp"):
                stale.unlink(missing_ok=True)
```

`web/src/types.ts`: `GroupMember` gains `media_type: "image" | "animated" | "video" | "other";` and

```ts
export interface FrameInfo {
  index: number;
  time_ms: number;
}

export interface FramesResponse {
  media_type: "image" | "animated" | "video" | "other";
  frames: FrameInfo[];
}
```

- [ ] **Step 4: Run the tests, lint, commit**

Run: Docker pytest on `tests/integration/test_api_preview.py tests/unit/test_decode.py`; `uv run pytest -q && uv run ruff check . && uv run ruff format . && (cd web && npm run lint)` — Expected: pass.

```bash
git add visualstore/api/files.py visualstore/db/groups.py visualstore/jobs/results.py tests/integration/test_api_preview.py tests/integration/test_api_groups.py web/src/types.ts
git commit -m "feat: frames listing and on-demand frame images; members carry their media type

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Frame strip in the comparison pane

**Files:**
- Create: `web/src/components/FrameStrip.tsx`, `web/src/components/frameTime.ts`, `web/src/components/frameTime.test.ts`
- Modify: `web/src/components/Compare.tsx`, `web/src/pages/GroupDetail.tsx`, `web/src/styles.css`, `web/e2e/start-server.sh`, `web/e2e/smoke.spec.ts`

**Interfaces:**
- `formatFrameTime(ms: number) -> string` — `"0.0 s"`, `"1.5 s"`, `"1:05.0"` for ≥ 60 s (m:ss.t).
- `<FrameStrip name fileId picked onPick />` — fetches `/files/{id}/frames` once per `fileId`, renders nothing for a single frame, otherwise one button per frame with `<img src=/frame/{index}>` (64 px) and the time label; the picked frame is marked `active`.
- `<Compare … leftFrame rightFrame />` — when a frame index is given the image source is `/frame/{index}` (fallback chain: frame → thumbnail → none).
- `GroupDetail` keeps `picked: Record<string, number>` (file id → frame index); strips render under the pane for the selected member and the target member when their `media_type` is `animated` or `video`; clicking a frame sets `picked[fileId]`.

- [ ] **Step 1: Write the failing unit test**

`web/src/components/frameTime.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatFrameTime } from "./frameTime";

describe("formatFrameTime", () => {
  it("shows tenths of seconds and minutes past sixty seconds", () => {
    expect(formatFrameTime(0)).toBe("0.0 s");
    expect(formatFrameTime(1500)).toBe("1.5 s");
    expect(formatFrameTime(18750)).toBe("18.8 s");
    expect(formatFrameTime(65_040)).toBe("1:05.0");
    expect(formatFrameTime(3_600_000)).toBe("60:00.0");
  });
});
```

Run: `cd web && npm test -- --run` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement**

`web/src/components/frameTime.ts`:

```ts
export function formatFrameTime(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}:${rest.toFixed(1).padStart(4, "0")}`;
}
```

`web/src/components/FrameStrip.tsx`:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import type { FramesResponse } from "../types";
import { formatFrameTime } from "./frameTime";

export default function FrameStrip({ name, fileId, picked, onPick }: { name: string; fileId: string; picked: number | null; onPick: (index: number) => void }) {
  const [frames, setFrames] = useState<FramesResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    setFrames(null);
    void get<FramesResponse>(`/api/p/${name}/files/${fileId}/frames`)
      .then((f) => {
        if (!cancelled) setFrames(f);
      })
      .catch(() => {
        if (!cancelled) setFrames({ media_type: "other", frames: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [name, fileId]);
  if (!frames || frames.frames.length < 2) return null;
  return (
    <div className="frame-strip" role="list" aria-label="Frames">
      {frames.frames.map((f) => (
        <button
          key={f.index}
          type="button"
          role="listitem"
          className={picked === f.index ? "active" : ""}
          onClick={() => onPick(f.index)}
          title={`Frame ${f.index} at ${formatFrameTime(f.time_ms)}`}
        >
          <img src={`/api/p/${name}/files/${fileId}/frame/${f.index}`} alt="" width={64} height={64} />
          <span>{formatFrameTime(f.time_ms)}</span>
        </button>
      ))}
    </div>
  );
}
```

`web/src/components/Compare.tsx`: `PreviewImage` takes `frame: number | null`; its state becomes `"main" | "thumbnail" | "none"`; the main path is `frame === null ? "preview" : `frame/${frame}``; the `useEffect` reset depends on `[fileId, frame]`; the class stays `preview preview-preview` for the main image (so the e2e selectors keep working) and `preview preview-thumbnail` for the fallback. `Compare` gains props `leftFrame?: number | null; rightFrame?: number | null` (default `null`) and passes them through.

`web/src/pages/GroupDetail.tsx`: `import FrameStrip from "../components/FrameStrip";`, state `const [picked, setPicked] = useState<Record<string, number>>({});`, pass `leftFrame={picked[selectedId] ?? null}` and `rightFrame={targetId ? (picked[targetId] ?? null) : null}` to `<Compare>`, and after it:

```tsx
          {[selectedId, targetId].filter((id): id is string => id !== null).map((id) => {
            const member = group.members.find((m) => m.file_id === id);
            if (!member || (member.media_type !== "animated" && member.media_type !== "video")) return null;
            return (
              <div key={id} className="strip-block">
                <span className="muted">Frames of {member.relative_path}</span>
                <FrameStrip name={name} fileId={id} picked={picked[id] ?? null} onPick={(index) => setPicked({ ...picked, [id]: index })} />
              </div>
            );
          })}
```

`styles.css`: `.frame-strip { display: flex; gap: 0.4rem; overflow-x: auto; padding: 0.25rem 0; } .frame-strip button { display: grid; justify-items: center; gap: 0.15rem; padding: 0.2rem; font-size: 0.75rem; } .frame-strip button img { object-fit: cover; border-radius: 0.2rem; } .strip-block { margin-top: 0.5rem; }`.

- [ ] **Step 3: Extend the e2e**

`web/e2e/start-server.sh`: replace the blue pair with a video and its first frame. Remove the two `sub2/d.png` / `sub2/e.png` lines and the `sub2` mkdir; after the Python block add:

```bash
# A solid navy clip (far from red and green under the tiny model) and its first frame as a
# still: they must land in the same group. ffmpeg comes from the host or from the test image.
clip_cmd=(ffmpeg -nostdin -hide_banner -loglevel error -y)
if command -v ffmpeg >/dev/null 2>&1; then
  "${clip_cmd[@]}" -f lavfi -i "color=c=0x000080:size=160x120:rate=10" -t 1.5 -pix_fmt yuv420p -movflags +faststart "$tmp/media/lib/sub2/clip.mp4"
  "${clip_cmd[@]}" -ss 0 -i "$tmp/media/lib/sub2/clip.mp4" -frames:v 1 "$tmp/media/lib/sub2/still.png"
else
  docker run --rm --user "$(id -u):$(id -g)" -v "$tmp:$tmp" visualstore:test "${clip_cmd[@]}" -f lavfi -i "color=c=0x000080:size=160x120:rate=10" -t 1.5 -pix_fmt yuv420p -movflags +faststart "$tmp/media/lib/sub2/clip.mp4"
  docker run --rm --user "$(id -u):$(id -g)" -v "$tmp:$tmp" visualstore:test "${clip_cmd[@]}" -ss 0 -i "$tmp/media/lib/sub2/clip.mp4" -frames:v 1 "$tmp/media/lib/sub2/still.png"
fi
```

(keep `mkdir -p "$tmp/media/lib/sub2"`).

`web/e2e/smoke.spec.ts`, second test: the queue holds the identical red pair and the near-duplicate {clip, still} group. Replace the "2 files" `.first()` click with the identical row: `await page.getByRole("row", { name: /identical/ }).getByRole("link", { name: "2 files" }).click();` (keep the `toHaveCount(2)` wait before it). After returning to the queue (the `page.goto("/p/demo/groups")` line) and before the bulk block, insert:

```ts
  await page.getByRole("row", { name: /near duplicate/ }).getByRole("link", { name: "2 files" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");
  const strip = page.getByRole("list", { name: "Frames" });
  await expect(strip.getByRole("listitem")).toHaveCount(2, { timeout: 20_000 });
  await expect(strip.getByRole("listitem").nth(1)).toContainText("1.0 s");
  await strip.getByRole("listitem").nth(1).click();
  await expect(page.locator("img.preview-preview").first()).toHaveAttribute("src", /\/frame\/1$/);
  await expect.poll(() => page.locator("img.preview-preview").first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await page.goto("/p/demo/groups");
```

The bulk sentence stays `1 groups match, 1 get the default resolution (0 skipped), 1 files will be moved to the trash` (the clip group: `clip.mp4` sorts before `still.png`, so the still is deleted). In the trash step replace `e.png` with `still.png`.

- [ ] **Step 4: Run the checks and the e2e, commit**

Run: `cd web && npm run lint && npm test -- --run && npm run build && npm run e2e` — Expected: pass.

```bash
git add web/src/components/FrameStrip.tsx web/src/components/frameTime.ts web/src/components/frameTime.test.ts web/src/components/Compare.tsx web/src/pages/GroupDetail.tsx web/src/styles.css web/e2e/start-server.sh web/e2e/smoke.spec.ts
git commit -m "feat(web): frame strip for animated and video members; frames in the comparison pane

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Documentation and status

**Files:**
- Modify: `README.md`, `docs/plans/README.md`, `CLAUDE.md`, `docs/decisions/0007-m5-implementation-choices.md`

- [ ] **Step 1: README**

In the "Models" paragraph (or a new "Video and animated images" paragraph right after it): videos and animated images contribute sampled frames (up to `frames.max_per_file`, default 8; videos up to 8 s one frame per second, longer ones 8 spread frames plus the first keyframe); each frame costs one vector and one index entry (512 × 2 bytes in the index plus the HNSW links, about 1.5 KB per frame all told); a still that is a frame of a video lands in the same group as the video; frame extraction uses the image's ffmpeg with `media.ffmpeg_timeout_s`; the frame strip under the comparison pane switches the compared image to a frame.

- [ ] **Step 2: Status and facts**

`docs/plans/README.md`: milestone 5 → `shipped`; the resolution note: milestones 3 to 5 expanded (6 remains task-level). `CLAUDE.md` status line: milestones 1 to 5 shipped; milestone 6 (wrap-up) is next and its task-level plan must be expanded first. ADR-0007 "Facts learned while shipping": anything the tasks discovered (ffmpeg flags that differed, pyvips page quirks).

- [ ] **Step 3: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && (cd web && npm run lint && npm test -- --run && npm run build && npm run e2e)` and the Docker suite: `docker run --rm -v "$PWD/visualstore:/app/visualstore:ro" -v "$PWD/tests:/app/tests:ro" visualstore:test python -m pytest -p no:cacheprovider tests/unit/test_decode.py tests/unit/test_video.py tests/unit/test_animated.py tests/unit/test_fingerprint_vectors.py tests/unit/test_match.py tests/unit/test_vector_index.py tests/integration/test_api_preview.py tests/integration/test_still_vs_video.py -q` — Expected: all green.

```bash
git add README.md docs/plans/README.md CLAUDE.md docs/decisions/0007-m5-implementation-choices.md
git commit -m "docs: video and animated image support, milestone 5 status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

Spec coverage ([11 §13 item 5](../design/11-roadmap.md#13-milestones), [05 §7.2–7.3](../design/05-pipeline.md)): animated decode and sampling (Tasks 1, 3, 4); video extraction and sampling with keyframe (Tasks 1, 2, 4); one vector per frame with `frame_time_ms` (Task 4; storage from milestone 3); middle-frame thumbnail for video (Task 4); stills match video (Task 5); webp by content (Task 4); frame strip in group detail (Tasks 6, 7); §9.5 memory note (Task 8).

Review Focus → tests: 1 `test_truncated_video_yields_the_decodable_frames_and_no_temp_files`; 2 `test_frames_follow_the_sampling_rules_for_each_backend`; 3 `test_a_still_cut_from_a_video_lands_in_the_near_duplicate_band`; 4 `test_allocate_keys_retires_frames_that_vanished` and `test_sink_replaces_frames_and_retires_keys_when_fewer_frames_come_back`; 5 `test_frames_listing_and_frame_images` plus the e2e strip steps.

Names checked across tasks: `Frame`, `sample_indices`, `sample_times`, `sampling_params`; `Decoded.pages`, `normalise`, `image_from_png`; `Probe`, `probe`, `count_frames`, `frame_png`, `frames_at_times`, `frames_at_indices`; `backend`, `page_count`, `page_times_ms`, `load_pages`, `animated.frames`; `_embed_frames`, `_frames_for`; `allocate_keys`; routes `/files/{id}/frames`, `/files/{id}/frame/{index}`; `_cached_render`; `GroupMember.media_type`, `FrameInfo`, `FramesResponse`, `formatFrameTime`, `FrameStrip`, `Compare.leftFrame/rightFrame`; settings `frames.max_per_file`, `media.ffmpeg_timeout_s`.
