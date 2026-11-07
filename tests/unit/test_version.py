import re
from pathlib import Path

import visualstore


def test_reported_version_is_the_pyproject_version():
    """Audit 2026-11-07: the 0.2.0 image announced itself as 0.1.0 (a hand-maintained string)."""
    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    [declared] = re.findall(r'^version = "([^"]+)"$', text, flags=re.M)
    assert visualstore.__version__ == declared
