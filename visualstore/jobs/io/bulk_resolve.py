"""Bulk resolution job (design §8.4, ADR-0006): apply the planned default decisions to each
group after re-validating it, trash inline, report progress per group."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime

from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.bulk import signature
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs.bulkplan import plan_for_group
from visualstore.jobs.io.actions import ActionError, execute_action
from visualstore.jobs.resolve import commit_decisions
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX


def _check(conn, item: dict) -> tuple[str | None, object]:
    """Returns (skip reason, group row)."""
    gid = id_from_str(item["group_id"])
    row = groups_db.get_group(conn, gid)
    if row is None or row["status"] != "open":
        return "gone", None
    members = {bytes(m["file_id"]) for m in groups_db.members(conn, gid)}
    if signature(members) != item["signature"]:
        return "members changed", row
    plan = plan_for_group(conn, row)
    planned = {id_from_str(t) for t in item["delete"]}
    if plan is None or set(plan.delete) != planned:
        return "rules changed", row
    return None, row


def run_bulk_resolve(ctx: IoContext) -> dict:
    items = ctx.params.get("groups", [])
    result: dict = {"resolved": 0, "trashed": 0, "skipped": []}
    now = datetime.now(UTC)
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        for i, item in enumerate(items, start=1):
            reason, row = _check(conn, item)
            if reason:
                result["skipped"].append({"group_id": item["group_id"], "reason": reason})
            else:
                gid = bytes(row["id"])
                planned = {id_from_str(t) for t in item["delete"]}
                decisions = {
                    bytes(m["file_id"]): ("delete" if bytes(m["file_id"]) in planned else "keep")
                    for m in groups_db.members(conn, gid)
                }
                trash = commit_decisions(conn, gid, decisions)
                try:
                    done = execute_action(
                        conn,
                        vconn,
                        ctx,
                        {"group_id": id_to_str(gid), "trash": [id_to_str(f) for f in trash]},
                        now,
                    )
                except ActionError as exc:
                    result["skipped"].append({"group_id": item["group_id"], "reason": str(exc)})
                    continue
                result["trashed"] += done["trashed"]
                if done["skipped"]:
                    result["skipped"].append(
                        {
                            "group_id": item["group_id"],
                            "reason": f"{len(done['skipped'])} file(s) skipped",
                        }
                    )
                else:
                    result["resolved"] += 1
            ctx.report(
                {"done": i, "total": len(items), "message": f"{result['trashed']} files trashed"}
            )
        if result["trashed"] and not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    return result
