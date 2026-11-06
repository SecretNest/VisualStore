"""Maintenance job (design §6.2): orphan derived files, frames of unknown files, ANALYZE,
and VACUUM when the free-page ratio justifies it. Idempotent; safe to interrupt."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_from_str
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_MAINTENANCE


def _known_ids(conn: sqlite3.Connection) -> set[bytes]:
    return {bytes(r["id"]) for r in conn.execute("SELECT id FROM file")}


def _id_of(path: Path) -> bytes | None:
    # frame images are named <uuid>-<frame index>.webp; uuids themselves contain hyphens
    text = path.stem.rsplit("-", 1)[0] if path.parent.parent.name == "frames" else path.stem
    try:
        return id_from_str(text)
    except ValueError:
        return None


def _sweep(directory: Path, known: set[bytes], ctx: IoContext, label: str) -> int:
    removed = 0
    if not directory.is_dir():
        return 0
    for path in directory.rglob("*.webp"):
        owner = _id_of(path)
        if owner is None or owner in known:
            continue
        path.unlink(missing_ok=True)
        removed += 1
        if removed % 500 == 0:
            ctx.report({"done": removed, "total": 0, "message": f"{label}: {removed} removed"})
    return removed


def _free_ratio(conn: sqlite3.Connection) -> float:
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return (free / pages) if pages else 0.0


def _vacuum_if_needed(conn: sqlite3.Connection, ratio: float) -> tuple[bool, float]:
    free = _free_ratio(conn)
    conn.execute("ANALYZE")
    if free > ratio:
        conn.isolation_level = None  # VACUUM cannot run inside a transaction
        conn.execute("VACUUM")
        conn.isolation_level = ""
        return True, free
    return False, free


def run_maintenance(ctx: IoContext) -> dict:
    ratio = float(ctx.settings.get("maintenance.vacuum_free_ratio", 0.2))
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        known = _known_ids(conn)
        thumbs = _sweep(ctx.paths.thumbs, known, ctx, "thumbnails")
        frames_dir = ctx.paths.previews / "frames"
        frame_images = _sweep(frames_dir, known, ctx, "frame images")
        previews = 0
        if ctx.paths.previews.is_dir():
            for sub in ctx.paths.previews.iterdir():
                if sub.name != "frames":
                    previews += _sweep(sub, known, ctx, "previews")
        unknown = fp.file_ids_with_frames(vconn) - known
        unknown_frames = 0
        if unknown:
            unknown_frames = fp.count_frames_of(vconn, unknown)
            fp.delete_for_files(vconn, unknown)
            if not (
                jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")
            ):
                jobs_db.enqueue(
                    conn, type="rebuild_index", kind="io", priority=PRIORITY_MAINTENANCE
                )
        vacuumed_meta, free_meta = _vacuum_if_needed(conn, ratio)
        vacuumed_vec, free_vec = _vacuum_if_needed(vconn, ratio)
    return {
        "orphan_thumbnails": thumbs,
        "orphan_previews": previews,
        "orphan_frame_images": frame_images,
        "unknown_file_frames": int(unknown_frames),
        "analyzed": True,
        "vacuumed": vacuumed_meta or vacuumed_vec,
        "free_ratio": round(max(free_meta, free_vec), 4),
    }
