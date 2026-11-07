import os
import subprocess
import sys


def test_missing_password_exits_1_with_explanation():
    env = {k: v for k, v in os.environ.items() if not k.startswith("VISUALSTORE_")}
    result = subprocess.run(
        [sys.executable, "-m", "visualstore"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 1
    assert "VISUALSTORE_PASSWORD" in result.stderr


def _image_present() -> bool:
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", "visualstore:test"], capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def test_container_entrypoint_drops_privileges_and_sets_home():
    """ADR-0008: the image runs as PUID:PGID with a writable HOME (docker only)."""
    import pytest

    if not _image_present():
        pytest.skip("visualstore:test image not built")
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "PUID=1234",
            "-e",
            "PGID=1234",
            "visualstore:test",
            "sh",
            "-c",
            "id -u; id -g; echo $HOME",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1234", "1234", "/tmp"]
