import pytest

from visualstore.db import actions, files, folders, groups, pairs
from visualstore.db.connection import connect
from visualstore.db.groups import NewGroup
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "p.db")
    migrate(conn, migrations_dir("profile"))
    (tmp_path / "arch").mkdir()
    (tmp_path / "inbox").mkdir()
    arch = folders.create_folder(
        conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all"
    )
    inbox = folders.create_folder(
        conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    ids = [
        files.insert_file(conn, folder_id=f, directory=d, name=n, extension_id=ext, size=1, mtime=1)
        for f, d, n in [
            (arch, "", "a.jpg"),
            (inbox, "x", "b.jpg"),
            (inbox, "", "c.jpg"),
            (arch, "", "d.jpg"),
        ]
    ]
    return conn, arch, inbox, ids


def test_sha1_pair_upsert_keeps_judgment_and_orders_ids(db):
    conn, _, _, (a, b, c, _) = db
    pairs.upsert_sha1_pair(conn, b, a)
    [row] = pairs.pairs_of_file(conn, a)
    assert (row["file_a"], row["file_b"]) == pairs.ordered(a, b)
    assert row["source"] == "sha1" and row["distance"] == 0.0 and row["judgment"] == "none"
    assert pairs.set_judgment(conn, a, b, "unrelated", "user")
    pairs.upsert_sha1_pair(conn, a, b)  # re-fingerprinting must not erase a human judgment
    assert pairs.pairs_of_file(conn, a)[0]["judgment"] == "unrelated"
    assert not pairs.set_judgment(conn, a, c, "unrelated", "user")  # no such pair
    pairs.upsert_sha1_pair(conn, b, c)
    assert len(pairs.edges_touching(conn, [a])) == 1
    assert len(pairs.edges_touching(conn, [b])) == 2
    d = pairs.pair_to_dict(pairs.pairs_of_file(conn, a)[0])
    assert isinstance(d["file_a"], str) and d["judgment_origin"] == "user"


def test_dirty_queue(db):
    conn, _, _, (a, b, *_) = db
    groups.mark_dirty(conn, [a, b, a])
    assert groups.dirty_count(conn) == 2
    assert set(groups.pop_dirty(conn)) == {a, b}
    assert groups.dirty_count(conn) == 0
    files.delete_files(conn, [a])  # cascade removes nothing from an empty queue, no error


def test_replace_groups_and_queries(db):
    conn, arch, inbox, (a, b, c, d) = db
    g1 = groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {a: "keep", b: "delete"})]
    )[0]
    assert groups.group_of_file(conn, a)["id"] == g1
    ms = groups.members(conn, g1)
    assert {m["file_id"] for m in ms} == {a, b}
    by_id = {m["file_id"]: m for m in ms}
    assert by_id[a]["role"] == "archive" and by_id[b]["directory"] == "x"
    assert by_id[a]["decision"] == "keep"
    assert groups.count_by_status(conn) == {
        "open": 1,
        "auto_resolved": 0,
        "resolved": 0,
        "dismissed": 0,
    }

    # a file can be in one group only: re-grouping {a, b, c} replaces g1
    [g2] = groups.replace_groups(
        conn,
        [g1],
        [
            NewGroup(
                frozenset({a, b, c}),
                0.0,
                True,
                "auto_resolved",
                {a: "keep", b: "delete", c: "delete"},
            )
        ],
    )
    assert groups.get_group(conn, g1) is None
    assert groups.get_group(conn, g2)["member_count"] == 3
    assert set(groups.member_file_ids(conn, [g2])) == {a, b, c}

    groups.set_decisions(conn, g2, {c: "keep"})
    assert {m["file_id"]: m["decision"] for m in groups.members(conn, g2)}[c] == "keep"
    groups.set_status(conn, g2, "resolved")
    assert groups.list_groups(conn, status="open") == []
    assert [groups.group_to_dict(r)["id"] for r in groups.list_groups(conn, status="resolved")]
    assert len(groups.list_groups(conn, folder_id=inbox)) == 1
    assert groups.list_groups(conn, folder_id=inbox, min_size=4) == []
    assert len(groups.list_groups(conn, has_identical=True)) == 1

    files.delete_files(conn, [b])  # member deletion cascades and marks the rest dirty
    assert groups.get_group(conn, g2)["member_count"] == 3  # stale until regroup recomputes
    assert set(groups.pop_dirty(conn)) == {a, c}


def test_actions_record_list_supersede(db):
    conn, _, _, (a, *_) = db
    act = actions.record(
        conn,
        "trash",
        a,
        {"path": "/m/a.jpg", "sha1": "00"},
        {"path": "/m/.visualstore-trash/2026-11/a.jpg"},
    )
    [row] = actions.list_trash(conn)
    assert row["id"] == act and actions.action_to_dict(row)["before"]["sha1"] == "00"
    assert actions.trash_older_than(conn, "2999-01-01T00:00:00+00:00") == [row]
    assert actions.trash_older_than(conn, "2000-01-01T00:00:00+00:00") == []
    actions.supersede(conn, act)
    assert actions.list_trash(conn) == []
    assert actions.get(conn, act)["superseded_at"] is not None
