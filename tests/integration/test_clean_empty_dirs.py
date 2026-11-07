"""Design §8.5: empty directories inside a Folder can be cleared by hand."""

from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.clean_empty_dirs import run_clean_empty_dirs


def test_clean_empty_directories_job_and_endpoint(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    lib = tmp_path / "lib"
    for d in ("keep", "empty/deeper", ".visualstore-trash/2026-11/empty", "ignored/empty"):
        (lib / d).mkdir(parents=True)
    (lib / "keep" / "a.jpg").write_bytes(b"x")
    folder = client.post(
        "/api/p/a/folders",
        json={"name": "L", "path": str(lib), "role": "inbox", "compare_scope": "all"},
    ).json()
    client.post(
        f"/api/p/a/folders/{folder['id']}/ignored", json={"directory": "ignored", "recursive": True}
    )
    r = client.post(f"/api/p/a/folders/{folder['id']}/clean-empty-directories")
    assert r.status_code == 201 and r.json()["type"] == "clean_empty_dirs"
    done = wait_for(client, "a", r.json()["id"])
    assert done["state"] == "done" and done["progress"]["result"]["removed"] == 4
    assert (
        (lib / "keep").is_dir() and not (lib / "empty").exists() and not (lib / "ignored").exists()
    )
    assert (lib / ".visualstore-trash" / "2026-11" / "empty").is_dir()  # the trash is never touched
    assert IO_HANDLERS["clean_empty_dirs"] is run_clean_empty_dirs
