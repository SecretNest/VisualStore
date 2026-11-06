"""Bulk rules over the review queue (design §8.4, ADR-0006)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.domain.bands import BANDS
from visualstore.domain.bulk import BulkPlan
from visualstore.ids import id_to_str
from visualstore.jobs.bulkplan import plan_for_filter
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(
    prefix="/api/p/{name}/groups/bulk", tags=["bulk"], dependencies=[Depends(require_auth)]
)


class BulkFilter(BaseModel):
    band: str | None = None
    folder_id: str | None = None
    has_identical: bool | None = None
    min_size: int | None = None


class BulkExecute(BulkFilter):
    token: str
    confirm: int | None = None


async def _plan(state, name: str, body: BulkFilter) -> BulkPlan:
    """Planning walks every open group: it runs in a thread on its own connection so a large
    queue does not freeze the event loop (audit B13)."""
    if body.band is not None and body.band not in BANDS:
        raise HTTPException(status_code=400, detail=f"band must be one of {BANDS}")
    folder_id = parse_id(body.folder_id) if body.folder_id else None
    paths = state.registry.paths(name)

    def work() -> BulkPlan:
        from contextlib import closing

        from visualstore.db.connection import connect

        with closing(connect(paths.metadata_db)) as conn:
            return plan_for_filter(
                conn,
                settings_db.all(conn),
                band=body.band,
                folder_id=folder_id,
                has_identical=body.has_identical,
                min_size=body.min_size,
            )

    return await asyncio.to_thread(work)


def _summary(plan: BulkPlan, threshold: int) -> dict:
    return {
        "matched": plan.matched,
        "planned": len(plan.groups),
        "skipped": plan.skipped,
        "files_to_delete": plan.files_to_delete,
        "folders": list(plan.folders),
        "token": plan.token,
        "threshold": threshold,
        "typed_confirmation_required": plan.files_to_delete > threshold,
    }


@router.post("/preview")
async def preview(body: BulkFilter, request: Request, name: str = Depends(get_profile)) -> dict:
    state = get_state(request)
    conn = state.registry.conn(name)
    plan = await _plan(state, name, body)
    return _summary(plan, int(settings_db.get(conn, "bulk.confirm_threshold")))


@router.post("/execute", status_code=201)
async def execute(body: BulkExecute, request: Request, name: str = Depends(get_profile)) -> dict:
    state = get_state(request)
    conn = state.registry.conn(name)
    plan = await _plan(state, name, body)
    if not plan.groups:
        raise HTTPException(
            status_code=400,
            detail="No group matches the filter with a complete default resolution; nothing to do.",
        )
    if plan.token != body.token:
        raise HTTPException(
            status_code=409, detail="The queue changed since the preview; preview again."
        )
    threshold = int(settings_db.get(conn, "bulk.confirm_threshold"))
    if plan.files_to_delete > threshold and body.confirm != plan.files_to_delete:
        raise HTTPException(
            status_code=400,
            detail=f"This run deletes {plan.files_to_delete} files; type that number to confirm.",
        )
    job_id = jobs_db.enqueue(
        conn,
        type="bulk_resolve",
        kind="io",
        priority=PRIORITY_USER,
        params={
            "groups": [
                {
                    "group_id": id_to_str(g.group_id),
                    "signature": g.signature,
                    "delete": [id_to_str(f) for f in g.delete],
                }
                for g in plan.groups
            ],
            "files_to_delete": plan.files_to_delete,
        },
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))
