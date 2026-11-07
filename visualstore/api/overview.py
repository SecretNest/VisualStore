"""Overview page data (design §8.1 item 1, milestone 1 subset)."""

import asyncio
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.backup import TARGETS, latest_backup_time

router = APIRouter(prefix="/api/p/{name}", tags=["overview"], dependencies=[Depends(require_auth)])


@router.get("/overview")
async def overview(request: Request, name: str = Depends(get_profile)) -> dict:
    state = get_state(request)
    conn = state.registry.conn(name)
    last_backup = {}
    for target in TARGETS:
        when = latest_backup_time(state.config.backup_dir, name, target)
        last_backup[target] = when.isoformat() if when else None
    counts = files_db.count_by_folder_and_state(conn)
    return {
        "profile": asdict(state.registry.get(name)),
        "folders": [
            {
                "id": id_to_str(bytes(f["id"])),
                "name": f["name"],
                "role": f["role"],
                "enabled": bool(f["enabled"]),
                "available": os.path.isdir(f["path"]),  # design §14: a wrong mount is visible
                "counts": counts.get(id_to_str(bytes(f["id"])), {}),
            }
            for f in folders_db.list_folders(conn)
        ],
        "groups": groups_db.count_by_status(conn),
        "dirty": groups_db.dirty_count(conn),
        "jobs": jobs_db.count_by_state(conn),
        "recent_jobs": [jobs_db.to_dict(r) for r in jobs_db.list_jobs(conn, limit=5)],
        "last_backup": last_backup,
        "usage_bytes": await asyncio.to_thread(state.registry.usage_bytes, name),
    }
