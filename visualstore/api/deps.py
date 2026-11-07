"""Request-scoped helpers shared by every router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from visualstore.app import AppState

COOKIE = "visualstore_session"


def get_state(request: Request) -> AppState:
    return request.app.state.vs


def session_ok(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    state = get_state(request)
    return bool(token and state.signer.verify(token, valid_from=state.sessions_valid_from))


async def require_auth(request: Request) -> None:
    if not session_ok(request):
        raise HTTPException(status_code=401, detail="Not logged in.")


async def get_profile(name: str, request: Request) -> str:
    if not get_state(request).registry.exists(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' does not exist.")
    return name
