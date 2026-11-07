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
    assert by_id["A"]["sha1"] == hashlib.sha1(b"aaa").hexdigest()
    assert by_id["A"]["state"] == "fingerprinted" and by_id["A"]["size"] == 3
    assert by_id["A"]["mtime"] == (tmp_path / "a.jpg").stat().st_mtime_ns
    assert by_id["B"]["sha1"] == hashlib.sha1(b"bbb").hexdigest()
    # Not found at compute time is not a verdict on the file: the sink leaves the row alone and
    # the next scan decides (audit 2026-11-07).
    assert by_id["C"]["state"] == "retry" and by_id["C"]["sha1"] is None
    assert "gone.jpg" in by_id["C"]["reason"]
    assert reports[-1]["done"] == 3 and reports[-1]["total"] == 3


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unopenable_file_is_reported_for_retry_not_raised(tmp_path):
    p = tmp_path / "locked.jpg"
    p.write_bytes(b"secret")
    p.chmod(0)
    try:
        [entry] = fingerprint(
            {"files": [{"id": "L", "path": str(p), "media_type": "image"}]}, lambda _: None
        )["files"]
    finally:
        p.chmod(0o600)
    # An open() error (EACCES here, ESTALE/EIO on a share) is retried, not persisted.
    assert entry["state"] == "retry" and entry["sha1"] is None


def test_registered():
    assert HANDLERS["fingerprint"] is fingerprint
    assert KIND_OF["fingerprint"] == "compute"
    for t in ("scan", "regroup", "apply_action", "purge_trash", "archive_move", "import_v1"):
        assert KIND_OF[t] == "io"
