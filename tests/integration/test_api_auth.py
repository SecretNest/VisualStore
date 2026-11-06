from fastapi.testclient import TestClient

from tests.integration.conftest import login
from visualstore.app import create_app
from visualstore.auth import SessionSigner
from visualstore.db import jobs as jobs_db
from visualstore.profiles import ProfileRegistry


def test_me_is_false_before_login(client):
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_protected_route_requires_login(client):
    assert client.get("/api/system/status").status_code == 401


def test_wrong_password_is_rejected(client):
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert "visualstore_session" not in client.cookies
    assert client.get("/api/auth/me").json() == {"authenticated": False}


def test_login_sets_httponly_cookie_and_unlocks_routes(client):
    response = client.post("/api/auth/login", json={"password": "pw"})
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "visualstore_session=" in set_cookie
    assert "HttpOnly" in set_cookie and "samesite=lax" in set_cookie.lower()
    assert client.get("/api/auth/me").json() == {"authenticated": True}
    status = client.get("/api/system/status")
    assert status.status_code == 200
    body = status.json()
    assert body["compute_processes"] == 1 and body["alive_workers"] == 1
    assert body["in_flight"] == 0 and body["profiles"] == 0
    assert body["version"]


def test_logout_clears_session(client):
    login(client)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json() == {"authenticated": False}
    assert client.get("/api/system/status").status_code == 401


def test_tampered_cookie_is_401_not_500(client):
    """Review Focus 5: garbage, empty, and foreign-key tokens are all plain 401s."""
    forged = SessionSigner(b"x" * 32).issue()
    for bad in ("garbage.value", "", forged, "a.b.c", "%00"):
        response = client.get(
            "/api/system/status", headers={"Cookie": f"visualstore_session={bad}"}
        )
        assert response.status_code == 401, bad


def test_sixth_failed_login_is_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "x"}).status_code == 429
    # even the right password is refused while locked out
    assert client.post("/api/auth/login", json={"password": "pw"}).status_code == 429


def test_jobs_left_running_are_requeued_on_start(config):
    reg = ProfileRegistry(config.data_dir)
    reg.open()
    reg.create("a")
    job_id = jobs_db.enqueue(reg.conn("a"), type="ping", kind="compute")
    jobs_db.mark_running(reg.conn("a"), job_id)
    reg.close()

    with TestClient(create_app(config)) as c:
        state = c.app.state.vs
        row = jobs_db.get(state.registry.conn("a"), job_id)
        assert row["attempts"] == 1
        assert row["state"] in ("queued", "running", "done")  # requeued, then picked up again
