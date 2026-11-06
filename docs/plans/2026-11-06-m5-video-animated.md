# Milestone 5 · Video and animated images — Task-level plan

> **Resolution:** task-level (see the note at the top of the milestone 3 plan). Expand after milestone 3 (vectors, index, match) is in place; milestone 4 is not a prerequisite except for the frame strip UI, which lands in the group detail page milestone 4 reshapes.

**Goal:** Animated images and videos take part in similarity with several sampled frames each, stills match video frames, and the group detail page shows a frame strip for multi-frame members.

**Architecture:** `media/frames.py` (pure) decides which frames to sample from a frame count or duration; `media/video.py` drives `ffmpeg` to extract exactly those frames as images; `media/animated.py` reads pages with pyvips (`n=-1`). The `fingerprint` handler produces one vector per sampled frame with `frame_index` and `frame_time_ms`; the index already holds frame-level entries with `(file_id, frame_index)` payload (milestone 3 key encoding), so matching needs no change beyond aggregation (already min-distance per file pair). Thumbnails for video use the middle frame.

**Spec:** [05 §7.2 (animated/video decode), §7.3 frame sampling, §7.4 payload per frame](../design/05-pipeline.md), [03 §5.1 Fingerprint](../design/03-data-model.md), [06 §8.3 frame strip](../design/06-review-ui.md#83-group-detail), [07 §9.5 memory](../design/07-operations.md#95-resources), [11 §13 item 5](../design/11-roadmap.md#13-milestones).

## Inputs from earlier milestones

- Milestone 3: multi-frame-capable `fingerprint` table and index keys; `fingerprint` handler with a per-frame result shape (`frames: [{index, time_ms, vector}]`).

## Decide before expanding

1. Frame extraction transport: ffmpeg writes PNG frames to a temp dir (simple, extra I/O) vs raw RGB over a pipe (faster, more code). Recommendation: temp dir with `-vf select` + `-vsync 0`, one ffmpeg call per file; measure before optimising.
2. Keyframe handling: design asks for "8 evenly spaced frames plus the first keyframe" for long videos. Recommendation: use `ffprobe` to read duration and the first keyframe timestamp; fall back to t=0 when ffprobe fails.
3. Memory cap for animated decoding: pyvips can load huge GIFs page by page; recommend a `media.max_frames_decoded` guard (default 2000 pages) above which only the sampled page indices are loaded via `page=` options.

## Global Constraints (additions)

- Sampling defaults (settings): `frames.max_per_file = 8`; animated ≤ 8 frames → all, else evenly spaced; video ≤ 8 s → 1 fps, else 8 evenly spaced + first keyframe; `frame_time_ms` recorded for every frame.
- ffmpeg runs with a timeout (`media.ffmpeg_timeout_s`, default 120) and `-nostdin`; a timeout marks the file `unreadable` with the reason, never hangs the child.
- A webp whose content is animated is treated as `animated` regardless of the extension table (design §5.1).

## Review Focus

1. A video with a corrupt tail (truncated file) must yield the frames ffmpeg can decode and mark the file `fingerprinted`, or `unreadable` if none, never leave temp files behind.
2. A GIF with a single frame and an APNG with 500 frames must both produce the right frame count (1 and 8) with monotonically increasing `frame_time_ms`.
3. A still image that is a screenshot of a video frame must match the video with a distance in the near-duplicate band in the synthetic test (same vector space claim from §7.3).
4. Deleting a video must remove every frame row and index entry; re-fingerprinting with a smaller `frames.max_per_file` must replace, not append, frames.
5. The frame strip must show frame times and switch the comparison image on click without re-downloading all frames.

## File structure

```
visualstore/
  media/frames.py               sample_indices(count, max) / sample_times(duration_s, max, first_keyframe_s)
  media/video.py                probe(path) -> duration, first_keyframe; extract_frames(path, times) -> list[Frame]
  media/animated.py             page_count(path); load_pages(path, indices) -> list[Frame]; is_animated_webp(path)
  jobs/compute/handlers.py      fingerprint: animated/video branches; middle-frame thumbnail for video
  db/fingerprints.py            replace_frames (delete + insert in one transaction)
  api/files.py                  + GET /files/{id}/frames (frame_index, time_ms) and GET /files/{id}/frame/{index} (jpeg of that frame, extracted on demand and cached)
web/src/components/FrameStrip.tsx
web/src/pages/GroupDetail.tsx   frame strip for multi-frame members; comparison target can be a frame
docker/Dockerfile               ffprobe present (ships with ffmpeg); test fixtures generated with ffmpeg at test time
tests/media/                    synthetic videos (ffmpeg testsrc) and animated images generated in fixtures
```

## Tasks

1. `media/frames.py` sampling rules with exhaustive unit tests (boundaries at 8 frames / 8 seconds, zero-length, single frame).
2. `media/video.py` probe + extraction with ffmpeg; tests generate a 3 s and a 20 s `testsrc` clip; truncated-file test (Review Focus 1); timeout test with a tiny timeout.
3. `media/animated.py` page counting, sampled page loading, animated-webp detection; tests with generated GIF/APNG/webp (Review Focus 2).
4. Fingerprint handler branches for animated and video, middle-frame thumbnail, per-frame result; `replace_frames` in the sink (Review Focus 4).
5. Still-vs-video matching integration test: render frames of a synthetic clip as stills, fingerprint both, match, assert a near-duplicate-band pair (Review Focus 3).
6. Frames API with on-demand frame extraction and cache under `previews/frames/`.
7. `FrameStrip.tsx` and group detail integration (Review Focus 5); Playwright: open a group containing the synthetic clip and click a frame.
8. Settings (`frames.max_per_file`, `media.ffmpeg_timeout_s`, `media.max_frames_decoded`) surfaced on the Jobs page; README section on video support and the memory cost per frame; plans README.

## Acceptance

- A library with mp4/webm/gif/apng files fingerprints without manual intervention; videos show frame strips; a still extracted from a video lands in the same group as the video.
- Index size grows by at most `frames.max_per_file` entries per video, as documented in §9.5.
