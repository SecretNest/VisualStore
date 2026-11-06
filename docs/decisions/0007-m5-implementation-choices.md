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
