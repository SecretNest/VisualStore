"""Default-rule plans computed from the database (design §8.4). The pure parts are in
visualstore.domain.bulk; this module reads groups and members."""

from __future__ import annotations

import sqlite3

from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db
from visualstore.domain.bands import band_bounds, thresholds_from
from visualstore.domain.bulk import BulkPlan, GroupPlan, build_plan, plan_group
from visualstore.domain.resolution import Member

MAX_GROUPS = 100_000


def plan_for_group(conn: sqlite3.Connection, row: sqlite3.Row) -> GroupPlan | None:
    gid = bytes(row["id"])
    rows = groups_db.members(conn, gid)
    members = [
        Member(
            bytes(r["file_id"]),
            bytes(r["folder_id"]),
            r["role"],
            r["folder_priority"],
            groups_db.member_to_dict(r)["relative_path"],
        )
        for r in rows
    ]
    names = {bytes(r["folder_id"]): r["folder_name"] for r in rows}
    inner = [
        p
        for p in pairs_db.inner_pairs(conn, {m.file_id for m in members})
        if p["judgment"] != "unrelated"
    ]
    all_identical = bool(inner) and all(p["source"] == "sha1" for p in inner)
    kept = [
        (bytes(p["file_a"]), bytes(p["file_b"])) for p in inner if p["judgment"] == "related_keep"
    ]
    return plan_group(gid, members, names, all_identical=all_identical, kept_edges=kept)


def plan_for_filter(
    conn: sqlite3.Connection,
    settings: dict,
    *,
    band: str | None,
    folder_id: bytes | None,
    has_identical: bool | None,
    min_size: int | None,
) -> BulkPlan:
    gt = le = None
    if band == "identical":
        has_identical = True
    elif band is not None:
        has_identical = False
        lo, hi = band_bounds(band, thresholds_from(settings))
        gt = None if lo == float("-inf") else lo
        le = None if hi == float("inf") else hi
    rows = groups_db.list_groups(
        conn,
        status="open",
        folder_id=folder_id,
        has_identical=has_identical,
        min_size=min_size,
        min_distance_gt=gt,
        min_distance_le=le,
        limit=MAX_GROUPS,
        offset=0,
    )
    return build_plan(len(rows), [plan_for_group(conn, r) for r in rows])
