"""Per-profile settings API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import settings as settings_db

router = APIRouter(
    prefix="/api/p/{name}/settings", tags=["settings"], dependencies=[Depends(require_auth)]
)


class SettingValue(BaseModel):
    value: Any


@router.get("")
def get_settings(request: Request, name: str = Depends(get_profile)) -> dict:
    return settings_db.all(get_state(request).registry.conn(name))


@router.put("/{key}")
def put_setting(
    key: str, body: SettingValue, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    try:
        settings_db.set(conn, key, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'.") from None
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return settings_db.all(conn)
