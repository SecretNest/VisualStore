import hashlib
import os

import pytest

from visualstore.jobs.compute.handlers import HANDLERS, fingerprint, sha1_of
from visualstore.jobs.types import KIND_OF


def test_sha1_of_streams_large_files(tmp_path):
    data = b"x" * (3 * 1024 * 1024 + 17)
    p = tmp_path / "big.bin"
    p.write_bytes(data)
    assert sha1_of(str(p)) == hashlib.sha1(data).digest()


def test_fingerprint_reports_each_file_and_progress(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"aaa")
    (tmp_path / "b.jpg").write_bytes(b"bbb")
    reports = []
    result = fingerprint(
        {
            "files": [
                {"id": "A", "path": str(tmp_path / "a.jpg"), "media_type": "image"},
                {"id": "B", "path": str(tmp_path / "b.jpg"), "media_type": "image"},
                {"id": "C", "path": str(tmp_path / "gone.jpg"), "media_type": "image"},
            ]
        },
        reports.append,
    )
    by_id = {f["id"]: f for f in result["files"]}
    assert by_id["A"] == {
        "id": "A",
        "sha1": hashlib.sha1(b"aaa").hexdigest(),
        "state": "fingerprinted",
        "size": 3,
    }
    assert by_id["B"]["sha1"] == hashlib.sha1(b"bbb").hexdigest()
    assert by_id["C"] == {"id": "C", "sha1": None, "state": "missing", "size": 0}
    assert reports[-1]["done"] == 3 and reports[-1]["total"] == 3


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_file_is_reported_not_raised(tmp_path):
    p = tmp_path / "locked.jpg"
    p.write_bytes(b"secret")
    p.chmod(0)
    try:
        [entry] = fingerprint(
            {"files": [{"id": "L", "path": str(p), "media_type": "image"}]}, lambda _: None
        )["files"]
    finally:
        p.chmod(0o600)
    assert entry["state"] == "unreadable" and entry["sha1"] is None


def test_registered():
    assert HANDLERS["fingerprint"] is fingerprint
    assert KIND_OF["fingerprint"] == "compute"
    for t in ("scan", "regroup", "apply_action", "purge_trash", "archive_move", "import_v1"):
        assert KIND_OF[t] == "io"
