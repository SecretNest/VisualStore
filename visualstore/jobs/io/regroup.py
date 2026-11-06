"""Regroup job (design §7.5): recompute connected components for dirty files, preselect
decisions (design §8.2) and hand identical groups to apply_action when allowed."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.db.groups import NewGroup
from visualstore.domain.grouping import Component, PairEdge, components
from visualstore.domain.resolution import Member, preselect
from visualstore.ids import id_to_str
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX

BATCH = 2000


def _same_sha1(conn: sqlite3.Connection, ids: set[bytes]) -> set[bytes]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT id FROM file WHERE state = 'fingerprinted' AND sha1 IN"
        f" (SELECT sha1 FROM file WHERE id IN ({marks}) AND sha1 IS NOT NULL)",
        list(ids),
    ).fetchall()
    return {bytes(r["id"]) for r in rows}


def _closure(conn: sqlite3.Connection, seeds: list[bytes], model_id: str | None) -> set[bytes]:
    """Seeds plus everything reachable through pairs, shared group membership or equal sha1."""
    scope: set[bytes] = set(seeds)
    frontier: set[bytes] = set(seeds)
    while frontier:
        edges = pairs_db.edges_touching(conn, frontier, model_id)
        reached = {bytes(e["file_a"]) for e in edges} | {bytes(e["file_b"]) for e in edges}
        group_ids = {
            bytes(g["id"]) for f in frontier if (g := groups_db.group_of_file(conn, f)) is not None
        }
        reached |= set(groups_db.member_file_ids(conn, group_ids))
        reached |= _same_sha1(conn, frontier)
        frontier = reached - scope
        scope |= frontier
    return scope


def _rehub_identical(conn: sqlite3.Connection, scope: set[bytes]) -> None:
    """Byte-identical edges follow from sha1 equality; the stored star pairs are a cache that
    must be rebuilt when a hub was deleted (final review I2)."""
    if not scope:
        return
    marks = ",".join("?" * len(scope))
    rows = conn.execute(
        f"SELECT id, sha1 FROM file WHERE id IN ({marks}) AND sha1 IS NOT NULL"
        " AND state = 'fingerprinted' ORDER BY created_at, rowid",
        list(scope),
    ).fetchall()
    buckets: dict[bytes, list[bytes]] = {}
    for r in rows:
        buckets.setdefault(bytes(r["sha1"]), []).append(bytes(r["id"]))
    for members in buckets.values():
        for other in members[1:]:
            pairs_db.upsert_sha1_pair(conn, members[0], other)


def _members(conn: sqlite3.Connection, ids: set[bytes]) -> dict[bytes, Member]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT f.id, f.folder_id, f.directory, f.name, fo.role, fo.priority"
        f" FROM file f JOIN folder fo ON fo.id = f.folder_id WHERE f.id IN ({marks})",
        list(ids),
    ).fetchall()
    return {
        bytes(r["id"]): Member(
            bytes(r["id"]),
            bytes(r["folder_id"]),
            r["role"],
            r["priority"],
            f"{r['directory']}/{r['name']}" if r["directory"] else r["name"],
        )
        for r in rows
    }


def _existing_group(conn: sqlite3.Connection, comp: Component):
    """The current group holding exactly this member set, if any: (status, decisions)."""
    any_member = next(iter(comp.members))
    g = groups_db.group_of_file(conn, any_member)
    if g is None:
        return None
    rows = groups_db.members(conn, bytes(g["id"]))
    if {bytes(r["file_id"]) for r in rows} != set(comp.members):
        return None
    return g["status"], {bytes(r["file_id"]): r["decision"] for r in rows}


def run_regroup(ctx: IoContext) -> dict:
    group_distance = float(ctx.settings.get("grouping.group_distance", 0.2))
    auto_setting = bool(ctx.settings.get("resolution.auto_execute_identical", True))
    model_id = ctx.settings.get("model.id")
    stats = {"components": 0, "groups": 0, "auto_executed": 0}
    with closing(ctx.open_metadata()) as conn:
        while True:
            seeds = groups_db.pop_dirty(conn, BATCH)
            if not seeds:
                break
            scope = _closure(conn, seeds, model_id)
            _rehub_identical(conn, scope)
            edges = [
                PairEdge(
                    bytes(r["file_a"]),
                    bytes(r["file_b"]),
                    r["distance"],
                    r["source"],
                    r["judgment"],
                )
                for r in pairs_db.edges_touching(conn, scope, model_id)
            ]
            comps = components(scope, edges, group_distance)
            stats["components"] += len(comps)
            old_groups = {
                bytes(g["id"]) for f in scope if (g := groups_db.group_of_file(conn, f)) is not None
            }
            members_by_id = _members(conn, scope)
            new_groups: list[NewGroup] = []
            to_execute: list[int] = []
            for comp in comps:
                inner = [
                    e
                    for e in edges
                    if e.a in comp.members and e.b in comp.members and e.judgment != "unrelated"
                ]
                all_identical = all(e.source == "sha1" for e in inner)
                existing = _existing_group(conn, comp)
                if existing is not None and existing[0] in ("resolved", "dismissed"):
                    status, decisions, auto = existing[0], existing[1], False
                elif inner and all(e.judgment == "related_keep" for e in inner):
                    status, decisions, auto = "resolved", dict.fromkeys(comp.members, "keep"), False
                else:
                    pre = preselect(
                        [members_by_id[m] for m in comp.members if m in members_by_id],
                        all_identical=all_identical,
                        auto_execute_identical=auto_setting,
                    )
                    status, decisions, auto = pre.status, pre.decisions, pre.auto_executable
                    kept_edges = [e for e in inner if e.judgment == "related_keep"]
                    if kept_edges:
                        # A human said "keep both" for these pairs: propagate keep along them
                        # and never execute such a group automatically (final review C2).
                        decisions = dict(decisions)
                        changed = True
                        while changed:
                            changed = False
                            for e in kept_edges:
                                for x, y in ((e.a, e.b), (e.b, e.a)):
                                    if decisions.get(x) == "keep" and decisions.get(y) != "keep":
                                        decisions[y] = "keep"
                                        changed = True
                        status, auto = "open", False
                if auto:
                    to_execute.append(len(new_groups))
                new_groups.append(
                    NewGroup(comp.members, comp.min_distance, comp.has_identical, status, decisions)
                )
            created = groups_db.replace_groups(conn, old_groups, new_groups)
            stats["groups"] += len(created)
            for index in to_execute:
                gid, ng = created[index], new_groups[index]
                jobs_db.enqueue(
                    conn,
                    type="apply_action",
                    kind="io",
                    priority=PRIORITY_INBOX,
                    params={
                        "group_id": id_to_str(gid),
                        "trash": [id_to_str(f) for f, d in ng.decisions.items() if d == "delete"],
                    },
                )
                stats["auto_executed"] += 1
            ctx.report(
                {"done": stats["groups"], "total": 0, "message": f"{stats['groups']} groups"}
            )
    return stats
