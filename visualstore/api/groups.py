"""Review queue, group detail, resolution and pair judgments (design §8.1 pages 3–4, §8.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.db import settings as settings_db
from visualstore.domain.bands import BANDS, band, band_bounds, thresholds_from
from visualstore.domain.neighbours import nearest
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}", tags=["groups"], dependencies=[Depends(require_auth)])


class Resolve(BaseModel):
    decisions: dict[str, str]


class Judgment(BaseModel):
    judgment: str


def _group_out(conn, row) -> dict:
    d = groups_db.group_to_dict(row)
    d["members"] = [groups_db.member_to_dict(m) for m in groups_db.members(conn, bytes(row["id"]))]
    t = thresholds_from(settings_db.all(conn))
    d["band"] = band(row["min_distance"], bool(row["has_identical"]), t)
    return d


def _group_or_404(conn, group_id: bytes):
    row = groups_db.get_group(conn, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such group.")
    return row


def _member_ids(conn, group_id: bytes) -> set[bytes]:
    return {bytes(m["file_id"]) for m in groups_db.members(conn, group_id)}


def _inner_pairs(conn, member_ids: set[bytes]) -> list:
    return [
        p
        for p in pairs_db.edges_touching(conn, member_ids)
        if bytes(p["file_a"]) in member_ids and bytes(p["file_b"]) in member_ids
    ]


@router.get("/groups")
async def list_groups(
    request: Request,
    name: str = Depends(get_profile),
    status: str | None = None,
    folder_id: str | None = None,
    has_identical: bool | None = None,
    min_size: int | None = None,
    band: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    if status is not None and status not in groups_db.STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {groups_db.STATUSES}")
    if band is not None and band not in BANDS:
        raise HTTPException(status_code=400, detail=f"band must be one of {BANDS}")
    conn = get_state(request).registry.conn(name)
    gt = le = None
    if band == "identical":
        has_identical = True
    elif band is not None:
        has_identical = False
        lo, hi = band_bounds(band, thresholds_from(settings_db.all(conn)))
        gt = None if lo == float("-inf") else lo
        le = None if hi == float("inf") else hi
    rows = groups_db.list_groups(
        conn,
        status=status,
        folder_id=parse_id(folder_id) if folder_id else None,
        has_identical=has_identical,
        min_size=min_size,
        min_distance_gt=gt,
        min_distance_le=le,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )
    return [_group_out(conn, r) for r in rows]


@router.get("/groups/counts")
async def group_counts(request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    t = thresholds_from(settings_db.all(conn))
    return {
        **groups_db.count_by_status(conn),
        "dirty": groups_db.dirty_count(conn),
        "bands": groups_db.count_by_band(conn, t.near_duplicate, t.variant),
    }


@router.get("/groups/{group_id}")
async def get_group(group_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    row = _group_or_404(conn, gid)
    members = groups_db.members(conn, gid)
    paths = {bytes(m["file_id"]): groups_db.member_to_dict(m)["relative_path"] for m in members}
    inner = _inner_pairs(conn, set(paths))
    near = nearest(
        paths,
        [(bytes(p["file_a"]), bytes(p["file_b"]), p["distance"], p["judgment"]) for p in inner],
    )
    return {
        "group": _group_out(conn, row),
        "pairs": [pairs_db.pair_to_dict(p) for p in inner],
        "neighbours": {
            id_to_str(m): ({"file_id": id_to_str(n[0]), "distance": n[1]} if n else None)
            for m, n in near.items()
        },
    }


@router.post("/groups/{group_id}/resolve")
async def resolve_group(
    group_id: str, body: Resolve, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    _group_or_404(conn, gid)
    members = _member_ids(conn, gid)
    decisions = {parse_id(k): v for k, v in body.decisions.items()}
    if any(v not in ("keep", "delete") for v in decisions.values()):
        raise HTTPException(status_code=400, detail="Decisions must be 'keep' or 'delete'.")
    undecided = sorted(id_to_str(m) for m in members if m not in decisions)
    if undecided:
        raise HTTPException(
            status_code=400,
            detail=f"Every member needs a decision; missing: {', '.join(undecided)}",
        )
    groups_db.set_decisions(conn, gid, {m: decisions[m] for m in members})
    kept = {m for m in members if decisions[m] == "keep"}
    for p in _inner_pairs(conn, kept):
        if p["judgment"] == "none":
            pairs_db.set_judgment(
                conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user"
            )
    trash = [id_to_str(m) for m in members if decisions[m] == "delete"]
    job = None
    if trash:
        job_id = jobs_db.enqueue(
            conn,
            type="apply_action",
            kind="io",
            priority=PRIORITY_USER,
            params={"group_id": group_id, "trash": trash},
        )
        job = jobs_db.to_dict(jobs_db.get(conn, job_id))
    else:
        groups_db.set_status(conn, gid, "resolved")
    return {"group": _group_out(conn, groups_db.get_group(conn, gid)), "job": job}


@router.post("/groups/{group_id}/dismiss")
async def dismiss_group(group_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    _group_or_404(conn, gid)
    members = _member_ids(conn, gid)
    for p in _inner_pairs(conn, members):
        pairs_db.set_judgment(conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user")
    groups_db.set_decisions(conn, gid, dict.fromkeys(members, "keep"))
    groups_db.set_status(conn, gid, "dismissed")
    return _group_out(conn, groups_db.get_group(conn, gid))


@router.put("/pairs/{file_a}/{file_b}/judgment")
async def judge_pair(
    file_a: str, file_b: str, body: Judgment, request: Request, name: str = Depends(get_profile)
) -> dict:
    if body.judgment not in pairs_db.JUDGMENTS:
        raise HTTPException(status_code=400, detail=f"judgment must be one of {pairs_db.JUDGMENTS}")
    conn = get_state(request).registry.conn(name)
    a, b = parse_id(file_a), parse_id(file_b)
    if not pairs_db.set_judgment(conn, a, b, body.judgment, "user"):
        raise HTTPException(status_code=404, detail="No such pair.")
    groups_db.mark_dirty(conn, [a, b])
    if not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_USER)
    fa, fb = pairs_db.ordered(a, b)
    row = conn.execute("SELECT * FROM pair WHERE file_a = ? AND file_b = ?", (fa, fb)).fetchone()
    return pairs_db.pair_to_dict(row)
