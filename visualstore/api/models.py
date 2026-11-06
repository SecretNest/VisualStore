"""Model registry, model switch and evaluation endpoints (design §7.6, §7.7)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import PRIORITY_USER
from visualstore.models.registry import load_registry

router = APIRouter(tags=["models"], dependencies=[Depends(require_auth)])
_REPORT_NAME = re.compile(r"^eval-[A-Za-z0-9._-]+\.(md|json)$")


class SwitchModel(BaseModel):
    model_id: str


class RunEval(BaseModel):
    db_path: str
    negatives: int = 5000


@router.get("/api/models")
async def list_models(request: Request) -> list[dict]:
    model_dir = get_state(request).config.model_dir
    registry = load_registry(model_dir)
    return [
        {
            "id": s.id,
            "dim": s.dim,
            "input_size": s.input_size,
            "description": s.description,
            "available": (model_dir / s.file_name()).is_file(),
        }
        for s in registry.values()
    ]


@router.post("/api/p/{name}/model-switch", status_code=201)
async def switch_model(
    body: SwitchModel, request: Request, name: str = Depends(get_profile)
) -> dict:
    if body.model_id not in load_registry(get_state(request).config.model_dir):
        raise HTTPException(status_code=400, detail=f"Unknown model '{body.model_id}'.")
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(
        conn,
        type="model_switch",
        kind="io",
        priority=PRIORITY_USER,
        params={"model_id": body.model_id},
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/api/p/{name}/eval", status_code=201)
async def run_eval(body: RunEval, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(
        conn, type="eval", kind="io", priority=PRIORITY_USER, params=body.model_dump()
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.get("/api/p/{name}/eval/reports")
async def list_reports(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    reports = get_state(request).registry.paths(name).root / "reports"
    if not reports.is_dir():
        return []
    out = [
        {"file": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime}
        for p in reports.iterdir()
        if _REPORT_NAME.match(p.name)
    ]
    out.sort(key=lambda d: d["time"], reverse=True)
    return out


@router.get("/api/p/{name}/eval/reports/{file}")
async def get_report(file: str, request: Request, name: str = Depends(get_profile)) -> FileResponse:
    if not _REPORT_NAME.match(file):
        raise HTTPException(status_code=404, detail="No such report.")
    path = get_state(request).registry.paths(name).root / "reports" / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such report.")
    media = "application/json" if file.endswith(".json") else "text/markdown"
    return FileResponse(path, media_type=media)
