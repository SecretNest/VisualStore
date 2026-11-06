"""Process-level status (pool health and counts) and the liveness probe the container uses."""

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from visualstore import __version__
from visualstore.api.deps import get_state, require_auth

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])
health_router = APIRouter(prefix="/api/system", tags=["system"])  # no auth: HEALTHCHECK probes it
HEALTH_MAX_AGE_S = 60.0


@router.get("/status")
async def status(request: Request) -> dict:
    state = get_state(request)
    return {
        "version": __version__,
        "compute_processes": state.pool.size,
        "alive_workers": state.pool.alive_count(),
        "in_flight": len(state.scheduler.in_flight),
        "profiles": len(state.registry.names()),
    }


@health_router.get("/health")
async def health(request: Request) -> JSONResponse:
    """200 while the scheduler loop ticks; 503 when it has not for HEALTH_MAX_AGE_S (ADR-0008).
    Reveals nothing but liveness."""
    state = get_state(request)
    age = state.scheduler.heartbeat_age()
    uptime = time.monotonic() - state.started_at
    stalled = (age is None and uptime > HEALTH_MAX_AGE_S) or (
        age is not None and age > HEALTH_MAX_AGE_S
    )
    body = {
        "status": "stalled" if stalled else "ok",
        "scheduler_age_s": None if age is None else round(age, 1),
    }
    return JSONResponse(status_code=503 if stalled else 200, content=body)
