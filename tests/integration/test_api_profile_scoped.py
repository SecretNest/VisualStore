import time

from tests.integration.conftest import login


def wait_for(client, name, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/p/{name}/jobs/{job_id}").json()
        if job["state"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def setup_profile(client, name="a"):
    login(client)
    assert client.post("/api/profiles", json={"name": name}).status_code == 201


def test_unknown_profile_is_404_everywhere(client):
    login(client)
    assert client.get("/api/p/ghost/jobs").status_code == 404
    assert client.get("/api/p/ghost/settings").status_code == 404
    assert client.get("/api/p/ghost/overview").status_code == 404
    assert client.get("/api/p/ghost/backups").status_code == 404


def test_ping_job_runs_through_the_real_pool(client):
    setup_profile(client)
    created = client.post(
        "/api/p/a/jobs", json={"type": "ping", "params": {"count": 2, "echo": "x"}}
    )
    assert created.status_code == 201
    job = created.json()
    assert job["state"] == "queued" and job["kind"] == "compute" and job["priority"] == 100
    finished = wait_for(client, "a", job["id"])
    assert finished["state"] == "done"
    assert finished["progress"]["result"]["pong"] == "x"
    assert finished["progress"]["done"] == 2
    listed = client.get("/api/p/a/jobs?state=done").json()
    assert [j["id"] for j in listed] == [job["id"]]


def test_unknown_job_type_is_400_and_bad_id_is_404(client):
    setup_profile(client)
    assert client.post("/api/p/a/jobs", json={"type": "teleport"}).status_code == 400
    assert client.get("/api/p/a/jobs/not-a-uuid").status_code == 404
    assert client.get("/api/p/a/jobs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_failed_job_can_be_retried_and_queued_job_cancelled(client):
    setup_profile(client)
    job = client.post("/api/p/a/jobs", json={"type": "fail", "params": {"message": "no"}}).json()
    failed = wait_for(client, "a", job["id"])
    assert failed["state"] == "failed" and failed["attempts"] == 3
    assert "RuntimeError: no" in failed["error"]
    assert client.post(f"/api/p/a/jobs/{job['id']}/cancel").status_code == 409
    retried = client.post(f"/api/p/a/jobs/{job['id']}/retry")
    assert retried.status_code == 200 and retried.json()["attempts"] == 0
    assert wait_for(client, "a", job["id"])["state"] == "failed"


def test_settings_get_and_put(client):
    setup_profile(client)
    assert client.get("/api/p/a/settings").json()["backup.metadata.keep"] == 14
    updated = client.put("/api/p/a/settings/backup.metadata.keep", json={"value": 30})
    assert updated.status_code == 200 and updated.json()["backup.metadata.keep"] == 30
    bad_type = client.put("/api/p/a/settings/backup.metadata.keep", json={"value": "30"})
    assert bad_type.status_code == 400
    assert client.put("/api/p/a/settings/nope", json={"value": 1}).status_code == 404
    # isolation: another profile keeps its default
    client.post("/api/profiles", json={"name": "b"})
    assert client.get("/api/p/b/settings").json()["backup.metadata.keep"] == 14


def test_backup_trigger_overview_and_listing(client, config):
    setup_profile(client)
    overview = client.get("/api/p/a/overview").json()
    assert overview["profile"]["name"] == "a"
    assert overview["last_backup"] == {"metadata": None, "vectors": None}
    assert overview["jobs"]["queued"] >= 0 and overview["usage_bytes"] > 0

    created = client.post("/api/p/a/backups", json={})
    assert created.status_code == 201
    job = wait_for(client, "a", created.json()["id"])
    assert job["state"] == "done", job
    assert job["type"] == "backup" and job["kind"] == "io"
    assert len(job["progress"]["result"]["files"]) == 2

    listed = client.get("/api/p/a/backups").json()
    assert {b["target"] for b in listed} == {"metadata", "vectors"}
    assert all((config.backup_dir / "a" / b["file"]).is_file() for b in listed)
    assert (config.backup_dir / "global.db").is_file()

    overview = client.get("/api/p/a/overview").json()
    assert overview["last_backup"]["metadata"] is not None
    assert overview["jobs"]["done"] >= 1
    assert overview["recent_jobs"][0]["type"] == "backup"


def test_overview_lists_folders_and_group_counts(client, tmp_path):
    setup_profile(client)
    (tmp_path / "lib").mkdir()
    body = {"name": "Lib", "path": str(tmp_path / "lib"), "role": "inbox", "compare_scope": "all"}
    folder = client.post("/api/p/a/folders", json=body).json()
    overview = client.get("/api/p/a/overview").json()
    assert overview["folders"] == [
        {"id": folder["id"], "name": "Lib", "role": "inbox", "enabled": True, "counts": {}}
    ]
    assert overview["groups"] == {"open": 0, "auto_resolved": 0, "resolved": 0, "dismissed": 0}
    assert overview["dirty"] == 0
