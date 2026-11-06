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
        fid = files.insert_file(
            conn,
            folder_id=folder,
            directory="",
            name=name,
            extension_id=ext,
            size=len(content),
            mtime=1,
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
    assert client.get("/api/p/a/groups/counts").json() == {
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
