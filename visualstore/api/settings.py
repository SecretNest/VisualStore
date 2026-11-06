"""Per-profile settings API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.jobs.types import PRIORITY_INBOX

INDEX_KEYS = ("index.quantization", "index.pca_dims")  # a change rebuilds the index (ADR-0008)


def _check_pca_dims(conn, value, model_dir) -> None:
    from visualstore.models.registry import ModelError, get_spec

    if not isinstance(value, int) or isinstance(value, bool):
        return  # the type error is reported by settings_db.set
    try:
        dim = get_spec(settings_db.get(conn, "model.id"), model_dir).dim
    except ModelError:
        return
    if value and not (16 <= value < dim):
        raise HTTPException(
            status_code=400, detail=f"index.pca_dims must be 0 or between 16 and {dim - 1}."
        )


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
    state = get_state(request)
    conn = state.registry.conn(name)
    if key == "index.pca_dims":
        _check_pca_dims(conn, body.value, state.config.model_dir)
    try:
        before = settings_db.get(conn, key)
        settings_db.set(conn, key, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'.") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    rebuild = False
    if key in INDEX_KEYS and settings_db.get(conn, key) != before:
        # A rebuild that is already running captured the old settings: queue another one.
        # Only an un-started (queued) rebuild will pick the new value up by itself.
        queued = conn.execute(
            "SELECT 1 FROM job WHERE type = 'rebuild_index' AND state = 'queued'"
        ).fetchone()
        if queued is None:
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        rebuild = True
    return {**settings_db.all(conn), "rebuild_queued": rebuild}
