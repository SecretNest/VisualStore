"""Single-password login (design §9.2)."""

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from visualstore.api.deps import COOKIE, get_state, session_ok
from visualstore.auth import password_matches

# Handlers are async so they run on the event-loop thread: the rate limiter's check and
# record then cannot interleave across concurrent requests (sync handlers run in a pool).
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    state = get_state(request)
    key = _client_key(request)
    if not state.limiter.allowed(key):
        raise HTTPException(
            status_code=429, detail="Too many failed logins. Try again in a minute."
        )
    if not password_matches(state.config.password, body.password):
        state.limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Wrong password.")
    state.limiter.reset(key)
    response.set_cookie(
        COOKIE,
        state.signer.issue(),
        max_age=state.signer.ttl_seconds,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    """Single password, single operator: logging out invalidates every session issued so far,
    not just this browser's cookie (a copied cookie would otherwise stay valid for 30 days)."""
    state = get_state(request)
    state.sessions_valid_from = time.time()
    state.registry.set_stamp("sessions_valid_from", state.sessions_valid_from)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict:
    return {"authenticated": session_ok(request)}
