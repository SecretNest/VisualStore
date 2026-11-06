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
