"""Process-level status: pool health and counts, shown on the profile management page."""

from fastapi import APIRouter, Depends, Request

from visualstore import __version__
from visualstore.api.deps import get_state, require_auth

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])


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
