"""Request-scoped helpers shared by every router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from visualstore.app import AppState

COOKIE = "visualstore_session"


def get_state(request: Request) -> AppState:
    return request.app.state.vs


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE)
    if not token or not get_state(request).signer.verify(token):
        raise HTTPException(status_code=401, detail="Not logged in.")


def get_profile(name: str, request: Request) -> str:
    if not get_state(request).registry.exists(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' does not exist.")
    return name
