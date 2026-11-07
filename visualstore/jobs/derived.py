"""Derived images of a file (thumbnail, preview, frame images): removed with the file row
(design §5.3) and when the file is recomputed."""

from __future__ import annotations

from visualstore.ids import id_to_str


def remove_derived(paths, file_id: bytes) -> None:
    text = id_to_str(file_id)
    (paths.thumbs / text[:2] / f"{text}.webp").unlink(missing_ok=True)
    (paths.previews / text[:2] / f"{text}.webp").unlink(missing_ok=True)
    frames_dir = paths.previews / "frames" / text[:2]
    if frames_dir.is_dir():
        for stale in frames_dir.glob(f"{text}-*.webp"):
            stale.unlink(missing_ok=True)
