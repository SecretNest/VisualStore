import json

import pytest

from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.regroup import run_regroup
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry

SHA = b"\x05" * 20


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    for d in ("arch", "arch2", "inbox"):
        (tmp_path / d).mkdir()
    arch = folders.create_folder(
        conn,
        name="Arch",
        path=str(tmp_path / "arch"),
        role="archive",
        compare_scope="all",
        priority=1,
    )
    arch2 = folders.create_folder(
        conn,
        name="Arch2",
        path=str(tmp_path / "arch2"),
        role="archive",
        compare_scope="all",
        priority=2,
    )
    inbox = folders.create_folder(
        conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all"
    )
    yield reg, conn, {"arch": arch, "arch2": arch2, "inbox": inbox}
    reg.close()


def add(conn, folder, name, sha=SHA):
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(
        conn, folder_id=folder, directory="", name=name, extension_id=ext, size=1, mtime=1
    )
    files.set_fingerprint_result(conn, fid, sha1=sha, state="fingerprinted")
    return fid


def connect(conn, *ids):
    hub = ids[0]
    for other in ids[1:]:
        pairs.upsert_sha1_pair(conn, hub, other)
    groups.mark_dirty(conn, ids)


def ctx_for(reg, **overrides):
    conn = reg.conn("a")
    settings = settings_db.all(conn)
    settings.update(overrides)
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings,
        params={},
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


AUTO_ON = {"resolution.auto_execute_identical": True}
AUTO_OFF = {"resolution.auto_execute_identical": False}


def test_archive_plus_inbox_identical_group_is_auto_executed(world):
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    result = run_regroup(ctx_for(reg, **AUTO_ON))
    assert result == {"components": 1, "groups": 1, "auto_executed": 1}
    g = groups.group_of_file(conn, a)
    assert g["status"] == "auto_resolved" and g["has_identical"] == 1
    decisions = {m["file_id"]: m["decision"] for m in groups.members(conn, g["id"])}
    assert decisions == {a: "keep", b: "delete"}
    [job] = jobs_db.list_jobs(conn, state="queued")
    assert job["type"] == "apply_action"
    assert json.loads(job["params"])["trash"] == [id_to_str(b)]
    assert groups.dirty_count(conn) == 0
    assert IO_HANDLERS["regroup"] is run_regroup


def test_setting_off_and_archive_only_groups_stay_open(world):
    """Review Focus 4."""
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    run_regroup(ctx_for(reg, **AUTO_OFF))
    assert groups.group_of_file(conn, a)["status"] == "open"
    assert jobs_db.list_jobs(conn, state="queued") == []

    c = add(conn, f["arch"], "c.jpg", sha=b"\x06" * 20)
    d = add(conn, f["arch"], "d.jpg", sha=b"\x06" * 20)
    connect(conn, c, d)
    run_regroup(ctx_for(reg, **AUTO_ON))
    g = groups.group_of_file(conn, c)
    assert g["status"] == "open"
    assert {m["decision"] for m in groups.members(conn, g["id"])} == {"undecided"}
    assert jobs_db.list_jobs(conn, state="queued") == []


def test_unrelated_judgment_splits_and_related_keep_closure_resolves(world):
    reg, conn, f = world
    a = add(conn, f["arch"], "a.jpg")
    b = add(conn, f["arch2"], "b.jpg")
    c = add(conn, f["arch"], "c.jpg")
    connect(conn, a, b, c)
    run_regroup(ctx_for(reg))
    g1 = groups.group_of_file(conn, a)
    assert g1["member_count"] == 3

    pairs.set_judgment(conn, a, c, "unrelated", "user")
    groups.mark_dirty(conn, [a, c])
    run_regroup(ctx_for(reg))
    assert groups.get_group(conn, g1["id"]) is None
    g2 = groups.group_of_file(conn, a)
    assert {m["file_id"] for m in groups.members(conn, g2["id"])} == {a, b}
    assert groups.group_of_file(conn, c) is None  # singleton, no group

    pairs.set_judgment(conn, a, b, "related_keep", "user")
    groups.mark_dirty(conn, [a])
    run_regroup(ctx_for(reg))
    g3 = groups.group_of_file(conn, a)
    assert g3["status"] == "resolved"
    assert {m["decision"] for m in groups.members(conn, g3["id"])} == {"keep"}


def test_resolved_status_survives_regroup_of_the_same_members(world):
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["arch"], "b.jpg")
    connect(conn, a, b)
    run_regroup(ctx_for(reg))
    g = groups.group_of_file(conn, a)
    groups.set_decisions(conn, g["id"], {a: "keep", b: "keep"})
    groups.set_status(conn, g["id"], "dismissed")
    groups.mark_dirty(conn, [a])
    run_regroup(ctx_for(reg))
    g2 = groups.group_of_file(conn, a)
    assert g2["status"] == "dismissed"
    assert {m["decision"] for m in groups.members(conn, g2["id"])} == {"keep"}


def test_member_deletion_shrinks_or_dissolves_the_group(world):
    reg, conn, f = world
    a = add(conn, f["arch"], "a.jpg")
    b = add(conn, f["inbox"], "b.jpg")
    c = add(conn, f["inbox"], "c.jpg")
    connect(conn, a, b, c)
    run_regroup(ctx_for(reg, **AUTO_OFF))
    files.delete_files(conn, [c])
    run_regroup(ctx_for(reg, **AUTO_OFF))
    assert groups.group_of_file(conn, a)["member_count"] == 2
    files.delete_files(conn, [b])
    run_regroup(ctx_for(reg, **AUTO_OFF))
    assert groups.group_of_file(conn, a) is None
    assert groups.count_by_status(conn)["open"] == 0


def test_related_keep_is_honoured_when_a_group_grows(world):
    """Final review C2: a user's 'keep both' must survive a new identical copy."""
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    run_regroup(ctx_for(reg, **AUTO_OFF))
    g = groups.group_of_file(conn, a)
    groups.set_decisions(conn, g["id"], {a: "keep", b: "keep"})
    pairs.set_judgment(conn, a, b, "related_keep", "user")
    groups.set_status(conn, g["id"], "resolved")

    c = add(conn, f["inbox"], "c.jpg")
    connect(conn, a, c)
    result = run_regroup(ctx_for(reg, **AUTO_ON))
    assert result["auto_executed"] == 0
    g2 = groups.group_of_file(conn, a)
    assert g2["status"] == "open"
    decisions = {m["file_id"]: m["decision"] for m in groups.members(conn, g2["id"])}
    assert decisions == {a: "keep", b: "keep", c: "delete"}
    assert jobs_db.list_jobs(conn, state="queued") == []


def test_identical_spokes_stay_grouped_after_the_hub_is_deleted(world):
    """Final review I2: sha1 equality, not the stored star, is the truth for identical edges."""
    reg, conn, f = world
    hub = add(conn, f["inbox"], "a.jpg")
    b, c = add(conn, f["arch"], "b.jpg"), add(conn, f["arch2"], "c.jpg")
    connect(conn, hub, b, c)
    run_regroup(ctx_for(reg, **AUTO_OFF))
    files.delete_files(conn, [hub])
    run_regroup(ctx_for(reg, **AUTO_OFF))
    g = groups.group_of_file(conn, b)
    assert g is not None and {m["file_id"] for m in groups.members(conn, g["id"])} == {b, c}
    assert g["has_identical"] == 1 and len(pairs.pairs_of_file(conn, b)) == 1


def test_vector_pairs_of_another_model_do_not_form_groups(world):
    """Final review, Important 3: distances of two models are never grouped together."""
    reg, conn, f = world
    a, b, c = (
        add(conn, f["arch"], "a.jpg", b"\x01" * 20),
        add(conn, f["inbox"], "b.jpg", b"\x02" * 20),
        add(conn, f["inbox"], "c.jpg", b"\x03" * 20),
    )
    active = settings_db.get(conn, "model.id")
    pairs.upsert_vector_pair(conn, a, b, 0.05, active)
    pairs.upsert_vector_pair(conn, b, c, 0.05, "some-older-model")
    groups.mark_dirty(conn, [a, b, c])
    run_regroup(ctx_for(reg, **AUTO_OFF))
    g = groups.group_of_file(conn, a)
    assert g is not None and g["member_count"] == 2
    assert groups.group_of_file(conn, c) is None
    assert len(pairs.pairs_of_file(conn, c)) == 1  # the stale pair row itself is kept


def test_dirty_marks_survive_a_failed_regroup(world, monkeypatch):
    """Audit B7: seeds are consumed only once their groups are written."""
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    monkeypatch.setattr(
        groups,
        "replace_groups",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError):
        run_regroup(ctx_for(reg, **AUTO_OFF))
    assert groups.dirty_count(conn) == 2


def test_a_closure_larger_than_the_sql_variable_limit_regroups(world):
    """Audit B7: 17 000 byte-identical placeholders in one bucket."""
    reg, conn, f = world
    ext = folders.extension_map(conn)["jpg"]["id"]
    ids = [bytes([i % 256, i // 256]) + b"\\x00" * 14 for i in range(17_000)]
    with conn:
        conn.executemany(
            "INSERT INTO file (id, folder_id, directory, name, extension_id, size, mtime, sha1, state, created_at)"
            " VALUES (?, ?, '', ?, ?, 1, 1, ?, 'fingerprinted', '2026-01-01T00:00:00+00:00')",
            [(fid, f["inbox"], f"p{i}.jpg", ext, SHA) for i, fid in enumerate(ids)],
        )
        conn.executemany(
            "INSERT INTO pair (file_a, file_b, distance, source) VALUES (?, ?, 0.0, 'sha1')",
            [(min(ids[0], other), max(ids[0], other)) for other in ids[1:]],
        )
    groups.mark_dirty(conn, ids)
    result = run_regroup(ctx_for(reg, **AUTO_OFF))
    assert result["groups"] >= 1 and groups.dirty_count(conn) == 0
    assert groups.group_of_file(conn, ids[123])["member_count"] == 17_000
    assert groups.count_by_status(conn).get("open") == 1


def test_group_ids_and_open_decisions_survive_a_regroup_that_changes_nothing(world):
    """Audit B8: confirmations racing a regroup are not dropped."""
    reg, conn, f = world
    a, b, c = (
        add(conn, f["inbox"], "a.jpg"),
        add(conn, f["inbox"], "b.jpg"),
        add(conn, f["arch"], "c.jpg", b"\\x09" * 20),
    )
    connect(conn, a, b)
    run_regroup(ctx_for(reg, **AUTO_OFF))
    g = groups.group_of_file(conn, a)
    groups.set_decisions(
        conn, g["id"], {a: "delete", b: "keep"}
    )  # the user's choice, apply_action pending
    groups.mark_dirty(conn, [a, c])  # a match touched a: its component is recomputed unchanged
    run_regroup(ctx_for(reg, **AUTO_OFF))
    again = groups.group_of_file(conn, a)
    assert again["id"] == g["id"]
    assert {m["file_id"]: m["decision"] for m in groups.members(conn, again["id"])} == {
        a: "delete",
        b: "keep",
    }
