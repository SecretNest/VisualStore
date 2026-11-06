import time

from visualstore.api.system import HEALTH_MAX_AGE_S


def test_health_needs_no_login_and_reflects_the_scheduler_heartbeat(client):
    deadline = time.time() + 5
    while time.time() < deadline:  # the first tick happens shortly after start-up
        ok = client.get("/api/system/health")
        if ok.status_code == 200:
            break
        time.sleep(0.1)
    assert ok.status_code == 200 and ok.json()["status"] == "ok"
    sched = client.app.state.vs.scheduler
    sched.poll_interval = 60  # keep the running loop from refreshing the heartbeat
    sched.last_tick = sched.last_tick - HEALTH_MAX_AGE_S - 1  # pretend the loop stalled
    stalled = client.get("/api/system/health")
    assert stalled.status_code == 503 and stalled.json()["status"] == "stalled"
