"""Per-profile settings API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.jobs.types import PRIORITY_INBOX

INDEX_KEYS = ("index.quantization", "index.pca_dims")  # a change rebuilds the index (ADR-0008)

router = APIRouter(
    prefix="/api/p/{name}/settings", tags=["settings"], dependencies=[Depends(require_auth)]
)


class SettingValue(BaseModel):
    value: Any


@router.get("")
async def get_settings(request: Request, name: str = Depends(get_profile)) -> dict:
    return settings_db.all(get_state(request).registry.conn(name))


@router.put("/{key}")
async def put_setting(
    key: str, body: SettingValue, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    try:
        before = settings_db.get(conn, key)
        settings_db.set(conn, key, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'.") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    rebuild = False
    if key in INDEX_KEYS and settings_db.get(conn, key) != before:
        active = jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(
            conn, "build_index"
        )
        if not active:
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        rebuild = True
    return {**settings_db.all(conn), "rebuild_queued": rebuild}
