"""Single-password login (design §9.2)."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from visualstore.api.deps import COOKIE, get_state
from visualstore.auth import password_matches

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
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
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    state = get_state(request)
    return {"authenticated": bool(token and state.signer.verify(token))}
