"""Remove empty directories inside a Folder (design §8.5, triggered by hand). The trash tree is
never touched; bottom-up so a directory emptied by its children goes too."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

from visualstore.db import folders as folders_db
from visualstore.domain.paths import TRASH_DIR
from visualstore.ids import id_from_str
from visualstore.jobs.scheduler import IoContext


def clean_empty_directories(root: Path) -> int:
    removed = 0
    for current, _dirs, _files in os.walk(root, topdown=False):
        rel = os.path.relpath(current, root)
        if rel == "." or rel.split(os.sep)[0] == TRASH_DIR:
            continue
        try:
            if not os.listdir(current):
                os.rmdir(current)
                removed += 1
        except OSError:
            continue
    return removed


def run_clean_empty_dirs(ctx: IoContext) -> dict:
    with closing(ctx.open_metadata()) as conn:
        folder = folders_db.get_folder(conn, id_from_str(ctx.params["folder_id"]))
    if folder is None:
        return {"removed": 0, "skipped": "folder missing"}
    root = Path(folder["path"])
    if not root.is_dir():
        return {"removed": 0, "skipped": "folder path not available"}
    return {"removed": clean_empty_directories(root)}
