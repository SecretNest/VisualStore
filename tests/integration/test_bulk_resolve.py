import hashlib

import pytest

from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.domain.bulk import signature
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.bulk_resolve import run_bulk_resolve
from visualstore.jobs.scheduler import IoContext


@pytest.fixture
def world(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    roots = {k: tmp_path / k for k in ("arch", "inbox", "arch2")}
    for r in roots.values():
        r.mkdir()
    f = {
        "arch": folders.create_folder(
            conn,
            name="Arch",
            path=str(roots["arch"]),
            role="archive",
            compare_scope="all",
            priority=1,
        ),
        "inbox": folders.create_folder(
            conn, name="Inbox", path=str(roots["inbox"]), role="inbox", compare_scope="all"
        ),
        "arch2": folders.create_folder(
            conn,
            name="Arch2",
            path=str(roots["arch2"]),
            role="archive",
            compare_scope="all",
            priority=2,
        ),
    }
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, name, content):
        (roots[folder] / name).write_bytes(content)
        st = (roots[folder] / name).stat()
        fid = files.insert_file(
            conn,
            folder_id=f[folder],
            directory="",
            name=name,
            extension_id=ext,
            size=st.st_size,
            mtime=st.st_mtime_ns,
        )
        files.set_fingerprint_result(
            conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted"
        )
        return fid

    def group(ids, identical, distance):
        hub = ids[0]
        for other in ids[1:]:
            if identical:
                pairs.upsert_sha1_pair(conn, hub, other)
            else:
                pairs.upsert_vector_pair(conn, hub, other, distance, "m")
        [gid] = groups.replace_groups(
            conn,
            [],
            [
                NewGroup(
                    frozenset(ids),
                    0.0 if identical else distance,
                    identical,
                    "open",
                    dict.fromkeys(ids, "undecided"),
                )
            ],
        )
        return gid

    a1, i1, i2 = (
        add("arch", "a1.jpg", b"x1"),
        add("inbox", "i1.jpg", b"x1"),
        add("inbox", "i2.jpg", b"x1"),
    )
    g_mixed = group([a1, i1, i2], True, 0.0)  # archive + inbox, identical: delete i1, i2
    i3, i4 = add("inbox", "i3.jpg", b"y1"), add("inbox", "i4.jpg", b"y2")
    g_inbox = group([i3, i4], False, 0.1)  # inbox only, near duplicate: delete i4
    a2, a3 = add("arch", "a2.jpg", b"z1"), add("arch", "a3.jpg", b"z2")
    g_arch = group([a2, a3], False, 0.25)  # archive only, one Folder: skipped
    return {
        "client": client,
        "reg": reg,
        "conn": conn,
        "roots": roots,
        "f": f,
        "ids": {"a1": a1, "i1": i1, "i2": i2, "i3": i3, "i4": i4, "a2": a2, "a3": a3},
        "g": {"mixed": g_mixed, "inbox": g_inbox, "arch": g_arch},
    }


def test_preview_counts_planned_and_skipped_groups(world):
    client = world["client"]
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    assert p["matched"] == 3 and p["planned"] == 2 and p["skipped"] == 1
    assert p["files_to_delete"] == 3 and p["folders"] == ["Inbox"]
    assert (
        p["threshold"] == 100
        and p["typed_confirmation_required"] is False
        and len(p["token"]) == 40
    )
    only_identical = client.post("/api/p/a/groups/bulk/preview", json={"band": "identical"}).json()
    assert only_identical["planned"] == 1 and only_identical["files_to_delete"] == 2
    by_folder = client.post(
        "/api/p/a/groups/bulk/preview", json={"folder_id": id_to_str(world["f"]["arch2"])}
    ).json()
    assert by_folder == {**by_folder, "matched": 0, "planned": 0, "files_to_delete": 0}


def test_execute_refuses_nothing_to_do_stale_tokens_and_missing_confirmation(world):
    """Review Focus 1 and 2."""
    client, conn = world["client"], world["conn"]
    nothing = client.post(
        "/api/p/a/groups/bulk/execute",
        json={"folder_id": id_to_str(world["f"]["arch2"]), "token": "x"},
    )
    assert nothing.status_code == 400 and "No group" in nothing.json()["detail"]
    assert jobs_db.list_jobs(conn) == []
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    stale = client.post("/api/p/a/groups/bulk/execute", json={"token": "0" * 40})
    assert stale.status_code == 409
    settings_db.set(conn, "bulk.confirm_threshold", 2)
    assert (
        client.post("/api/p/a/groups/bulk/preview", json={}).json()["typed_confirmation_required"]
        is True
    )
    unconfirmed = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"]})
    assert unconfirmed.status_code == 400 and "type" in unconfirmed.json()["detail"]
    wrong = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"], "confirm": 2})
    assert wrong.status_code == 400
    # the queue changes between preview and execute: the token no longer matches
    pairs.set_judgment(conn, world["ids"]["i3"], world["ids"]["i4"], "unrelated", "user")
    groups.replace_groups(conn, [world["g"]["inbox"]], [])
    changed = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"], "confirm": 3})
    assert changed.status_code == 409
    assert jobs_db.list_jobs(conn) == []


def test_execute_runs_one_job_that_trashes_and_resolves_each_planned_group(world):
    client, conn, roots, ids = world["client"], world["conn"], world["roots"], world["ids"]
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    job = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"]})
    assert job.status_code == 201 and job.json()["type"] == "bulk_resolve"
    done = wait_for(client, "a", job.json()["id"])
    assert done["state"] == "done", done
    result = done["progress"]["result"]
    assert result["resolved"] == 2 and result["trashed"] == 3 and result["skipped"] == []
    assert groups.get_group(conn, world["g"]["mixed"])["status"] == "resolved"
    assert groups.get_group(conn, world["g"]["inbox"])["status"] == "resolved"
    assert groups.get_group(conn, world["g"]["arch"])["status"] == "open"
    assert not (roots["inbox"] / "i1.jpg").exists() and not (roots["inbox"] / "i4.jpg").exists()
    assert (roots["inbox"] / "i3.jpg").exists() and (roots["arch"] / "a1.jpg").exists()
    assert files.get_file(conn, ids["i4"]) is None and files.get_file(conn, ids["i3"]) is not None
    assert IO_HANDLERS["bulk_resolve"] is run_bulk_resolve


def test_job_skips_groups_whose_members_or_rules_changed(world):
    """Review Focus 2 at execution time."""
    reg, conn, ids, g = world["reg"], world["conn"], world["ids"], world["g"]
    stale_sig = signature([ids["i3"], ids["i4"]])
    i5 = files.insert_file(
        conn,
        folder_id=world["f"]["inbox"],
        directory="",
        name="i5.jpg",
        extension_id=folders.extension_map(conn)["jpg"]["id"],
        size=1,
        mtime=1,
    )
    files.set_fingerprint_result(conn, i5, sha1=b"\x09" * 20, state="fingerprinted")
    groups.replace_groups(
        conn,
        [g["inbox"]],
        [NewGroup(frozenset({ids["i3"], ids["i4"], i5}), 0.1, False, "open", {})],
    )
    new_gid = groups.group_of_file(conn, ids["i3"])["id"]
    params = {
        "groups": [
            {
                "group_id": id_to_str(new_gid),
                "signature": stale_sig,
                "delete": [id_to_str(ids["i4"])],
            },
            {
                "group_id": id_to_str(g["mixed"]),
                "signature": signature([ids["a1"], ids["i1"], ids["i2"]]),
                "delete": [id_to_str(ids["i1"])],
            },  # planned delete set differs from the rules
            {"group_id": id_to_str(b"\x00" * 16), "signature": "", "delete": []},
        ]
    }
    ctx = IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params,
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )
    result = run_bulk_resolve(ctx)
    assert result["resolved"] == 0 and result["trashed"] == 0
    assert sorted(s["reason"] for s in result["skipped"]) == [
        "gone",
        "members changed",
        "rules changed",
    ]
    assert (
        files.get_file(conn, ids["i4"]) is not None and files.get_file(conn, ids["i1"]) is not None
    )
