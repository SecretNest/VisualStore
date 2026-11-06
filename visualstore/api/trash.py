"""Trash listing and restore (design §8.5)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import actions as actions_db
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(
    prefix="/api/p/{name}/trash", tags=["trash"], dependencies=[Depends(require_auth)]
)


class Restore(BaseModel):
    action_ids: list[str]


@router.get("")
async def list_trash(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    return [actions_db.action_to_dict(r) for r in actions_db.list_trash(conn)]


@router.post("/restore", status_code=201)
async def restore(body: Restore, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    for text in body.action_ids:
        parse_id(text)
    job_id = jobs_db.enqueue(
        conn,
        type="apply_action",
        kind="io",
        priority=PRIORITY_USER,
        params={"restore": body.action_ids},
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))
