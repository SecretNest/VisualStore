import hashlib
import time

import pytest

from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders, groups, pairs
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str


@pytest.fixture
def seeded(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    arch, inbox = tmp_path / "arch", tmp_path / "inbox"
    arch.mkdir()
    inbox.mkdir()
    fa = folders.create_folder(
        conn, name="Arch", path=str(arch), role="archive", compare_scope="all"
    )
    fi = folders.create_folder(
        conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, root, name, content):
        (root / name).write_bytes(content)
        st = (root / name).stat()
        fid = files.insert_file(
            conn,
            folder_id=folder,
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

    a = add(fa, arch, "a.jpg", b"same")
    b = add(fi, inbox, "b.jpg", b"same")
    c = add(fi, inbox, "c.jpg", b"same")
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.upsert_sha1_pair(conn, a, c)
    decisions = {a: "keep", b: "delete", c: "undecided"}
    [gid] = groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b, c}), 0.0, True, "open", decisions)]
    )
    return {"a": a, "b": b, "c": c, "gid": gid, "arch": fa, "inbox": fi, "inbox_dir": inbox}


def test_list_filter_counts_and_detail(client, seeded):
    s = seeded
    listed = client.get("/api/p/a/groups").json()
    assert len(listed) == 1 and listed[0]["member_count"] == 3 and len(listed[0]["members"]) == 3
    member = next(m for m in listed[0]["members"] if m["file_id"] == id_to_str(s["b"]))
    assert member["decision"] == "delete" and member["role"] == "inbox"
    assert member["relative_path"] == "b.jpg"
    assert client.get("/api/p/a/groups?status=resolved").json() == []
    assert client.get("/api/p/a/groups?status=bogus").status_code == 400
    query = f"folder_id={id_to_str(s['arch'])}&has_identical=true&min_size=3"
    assert len(client.get(f"/api/p/a/groups?{query}").json()) == 1
    counts = client.get("/api/p/a/groups/counts").json()
    assert {k: counts[k] for k in ("open", "auto_resolved", "resolved", "dismissed", "dirty")} == {
        "open": 1,
        "auto_resolved": 0,
        "resolved": 0,
        "dismissed": 0,
        "dirty": 0,
    }
    detail = client.get(f"/api/p/a/groups/{id_to_str(s['gid'])}").json()
    assert detail["group"]["id"] == id_to_str(s["gid"]) and len(detail["pairs"]) == 2
    missing = client.get("/api/p/a/groups/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_resolve_requires_every_decision_then_trashes(client, seeded):
    s = seeded
    gid = id_to_str(s["gid"])
    a, b, c = (id_to_str(s[k]) for k in ("a", "b", "c"))
    partial_ = client.post(
        f"/api/p/a/groups/{gid}/resolve", json={"decisions": {a: "keep", b: "delete"}}
    )
    assert partial_.status_code == 400 and c in partial_.json()["detail"]
    full = client.post(
        f"/api/p/a/groups/{gid}/resolve",
        json={"decisions": {a: "keep", b: "delete", c: "keep"}},
    )
    assert full.status_code == 200, full.text
    body = full.json()
    assert body["job"]["type"] == "apply_action" and body["job"]["params"]["trash"] == [b]
    finished = wait_for(client, "a", body["job"]["id"])
    assert finished["state"] == "done" and finished["progress"]["result"]["trashed"] == 1
    assert not (s["inbox_dir"] / "b.jpg").exists()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = client.get(f"/api/p/a/groups/{gid}")
        if detail.status_code == 200 and detail.json()["group"]["status"] == "resolved":
            break
        time.sleep(0.05)
    detail = client.get(f"/api/p/a/groups/{gid}")
    assert detail.status_code == 200 and detail.json()["group"]["status"] == "resolved"
    kept_pair = next(p for p in detail.json()["pairs"] if {p["file_a"], p["file_b"]} == {a, c})
    assert kept_pair["judgment"] == "related_keep" and kept_pair["judgment_origin"] == "user"


def test_dismiss_and_judgment(client, seeded):
    s = seeded
    gid = id_to_str(s["gid"])
    dismissed = client.post(f"/api/p/a/groups/{gid}/dismiss").json()
    assert dismissed["status"] == "dismissed"
    detail = client.get(f"/api/p/a/groups/{gid}").json()
    assert all(p["judgment"] == "related_keep" for p in detail["pairs"])
    a, b, c = (id_to_str(s[k]) for k in ("a", "b", "c"))
    judged = client.put(f"/api/p/a/pairs/{a}/{c}/judgment", json={"judgment": "unrelated"})
    assert judged.status_code == 200 and judged.json()["judgment"] == "unrelated"
    none_pair = client.put(f"/api/p/a/pairs/{b}/{c}/judgment", json={"judgment": "none"})
    assert none_pair.status_code == 404
    bad = client.put(f"/api/p/a/pairs/{a}/{c}/judgment", json={"judgment": "maybe"})
    assert bad.status_code == 400
    queued = [j for j in client.get("/api/p/a/jobs").json() if j["type"] == "regroup"]
    assert queued and queued[0]["priority"] == 100


def test_file_detail_and_content(client, seeded):
    s = seeded
    fid = id_to_str(s["b"])
    detail = client.get(f"/api/p/a/files/{fid}").json()
    assert detail["relative_path"] == "b.jpg" and detail["group_id"] == id_to_str(s["gid"])
    assert len(detail["pairs"]) == 1
    content = client.get(f"/api/p/a/files/{fid}/content")
    assert content.status_code == 200 and content.content == b"same"
    assert content.headers["content-type"].startswith("image/jpeg")
    (s["inbox_dir"] / "b.jpg").unlink()
    assert client.get(f"/api/p/a/files/{fid}/content").status_code == 404
    assert client.get("/api/p/a/files/00000000-0000-0000-0000-000000000000").status_code == 404


def test_groups_carry_bands_and_filter_by_band(client, seeded):
    s = seeded
    listed = client.get("/api/p/a/groups").json()
    assert listed[0]["band"] == "identical"
    assert client.get("/api/p/a/groups?band=identical").json()[0]["id"] == id_to_str(s["gid"])
    assert client.get("/api/p/a/groups?band=variant").json() == []
    assert client.get("/api/p/a/groups?band=nope").status_code == 400
    counts = client.get("/api/p/a/groups/counts").json()
    assert counts["bands"] == {"identical": 1, "near_duplicate": 0, "variant": 0, "candidate": 0}


def test_detail_carries_the_nearest_neighbour_per_member(client, seeded):
    s = seeded
    detail = client.get(f"/api/p/a/groups/{id_to_str(s['gid'])}").json()
    near = detail["neighbours"]
    a, b, c = (id_to_str(s[k]) for k in "abc")
    assert near[a] == {"file_id": b, "distance": 0.0}  # tie with c: b.jpg sorts first
    assert near[b] == {"file_id": a, "distance": 0.0}
    assert near[c] == {"file_id": a, "distance": 0.0}


def test_file_detail_lists_candidates_beyond_the_grouping_threshold(client, seeded):
    """Review Focus 5."""
    s = seeded
    conn = client.app.state.vs.registry.conn("a")
    add_extra = lambda name: files.insert_file(  # noqa: E731
        conn,
        folder_id=s["inbox"],
        directory="",
        name=name,
        extension_id=folders.extension_map(conn)["jpg"]["id"],
        size=1,
        mtime=1,
    )
    near, cand, far, bad = (
        add_extra("near.jpg"),
        add_extra("cand.jpg"),
        add_extra("far.jpg"),
        add_extra("bad.jpg"),
    )
    a = s["a"]
    pairs.upsert_vector_pair(
        conn, a, near, 0.1, "m"
    )  # within group_distance (0.3): grouped, not a candidate
    pairs.upsert_vector_pair(conn, a, cand, 0.4, "m")  # candidate
    pairs.upsert_vector_pair(conn, a, far, 0.6, "m")  # beyond candidate_distance (0.5)
    pairs.upsert_vector_pair(conn, a, bad, 0.35, "m")
    pairs.set_judgment(conn, a, bad, "unrelated", "user")
    d = client.get(f"/api/p/a/files/{id_to_str(a)}").json()
    assert d["media_type"] == "image" and d["folder_name"] == "Arch"
    assert [c["file_id"] for c in d["candidates"]] == [id_to_str(cand)]
    assert (
        d["candidates"][0]["relative_path"] == "cand.jpg" and d["candidates"][0]["distance"] == 0.4
    )
    assert d["candidates"][0]["group_id"] is None and d["group_id"] == id_to_str(s["gid"])


def test_move_and_rename_endpoints_validate_and_queue(client, seeded):
    s = seeded
    b = id_to_str(s["b"])
    arch = id_to_str(s["arch"])
    assert client.post(f"/api/p/a/files/{b}/rename", json={"name": "../x.jpg"}).status_code == 400
    assert client.post(f"/api/p/a/files/{b}/rename", json={"name": "c.jpg"}).status_code == 409
    bad_dir = client.post(
        f"/api/p/a/files/{b}/move", json={"folder_id": arch, "directory": "../up"}
    )
    assert bad_dir.status_code == 400
    nowhere = client.post(
        f"/api/p/a/files/{b}/move",
        json={"folder_id": "00000000-0000-4000-8000-000000000000", "directory": ""},
    )
    assert nowhere.status_code == 404
    job = client.post(f"/api/p/a/files/{b}/move", json={"folder_id": arch, "directory": ""})
    assert job.status_code == 201
    assert job.json()["params"]["move"] == [{"file_id": b, "folder_id": arch, "directory": ""}]
    done = wait_for(client, "a", job.json()["id"])
    assert done["state"] == "done" and done["progress"]["result"]["moved"] == 1
    assert client.get(f"/api/p/a/files/{b}").json()["folder_name"] == "Arch"
    renamed = client.post(f"/api/p/a/files/{b}/rename", json={"name": "b2.jpg"})
    assert renamed.status_code == 201
    assert wait_for(client, "a", renamed.json()["id"])["progress"]["result"]["renamed"] == 1
