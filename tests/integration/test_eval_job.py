import json
import sqlite3
import uuid

import numpy as np
import pytest

from tests.fixtures.v1db import SCHEMA
from visualstore.db import files, fingerprints, folders
from visualstore.db import settings as settings_db
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.eval import EvalError, run_eval
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry

MODEL = "sscd-resnet50-512-v1"


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return (v / np.linalg.norm(v)).astype(np.float16).tobytes()


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    for d in ("arch", "inbox"):
        (tmp_path / d).mkdir()
    arch = folders.create_folder(
        conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all"
    )
    inbox = folders.create_folder(
        conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    rng = np.random.default_rng(7)
    v1 = sqlite3.connect(tmp_path / "v1.db")
    v1.executescript(SCHEMA)
    ids = []
    for i in range(60):
        base = rng.normal(size=512)
        a, b = uuid.uuid4(), uuid.uuid4()
        for u, folder, noise in ((a, arch, 0.0), (b, inbox, 0.02)):
            # our id == the v1 Guid so the v1 rows map onto our rows
            files.insert_file(
                conn,
                folder_id=folder,
                directory="",
                name=f"{u}.jpg",
                extension_id=ext,
                size=1,
                mtime=1,
                file_id=u.bytes,
            )
            # pairs 50..59 are byte-identical copies (same sha1); every other file has its own sha1
            sha = bytes([i]) * 20 if i >= 50 or noise == 0 else bytes([100 + i]) * 20
            files.set_fingerprint_result(conn, u.bytes, sha1=sha, state="fingerprinted")
            fingerprints.replace_frames(
                vconn, u.bytes, MODEL, [(0, 0, unit(base + rng.normal(scale=noise, size=512)))]
            )
        ids.append((a, b))
    for a, b in ids[:50]:  # related pairs with a small distance
        v1.execute(
            "INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().bytes_le, a.bytes_le, b.bytes_le, 0.01, 1),
        )
    for (a, _), (_, b) in zip(ids[:30], ids[30:60], strict=True):  # unrelated: different bases
        v1.execute(
            "INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().bytes_le, a.bytes_le, b.bytes_le, 0.5, 2),
        )
    v1.execute(
        "INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().bytes_le, uuid.uuid4().bytes_le, ids[0][0].bytes_le, 0.1, 1),
    )  # orphan
    v1.commit()
    v1.close()
    yield reg, conn, tmp_path
    reg.close()


def ctx_for(reg, params):
    conn = reg.conn("a")
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params,
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def test_eval_separates_positives_from_negatives_and_writes_reports(world):
    """Review Focus 5."""
    reg, conn, tmp_path = world
    before = conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0]
    result = run_eval(
        ctx_for(reg, {"db_path": str(tmp_path / "v1.db"), "negatives": 200, "seed": 1})
    )
    assert (
        result["positives"] == 50
        and result["positives_identical"] == 10
        and result["negatives_labelled"] == 30
        and result["negatives_random"] == 200
    )
    assert result["orphans"] == 1
    assert result["suggested"]["near_duplicate"] <= result["suggested"]["variant"] < 0.5
    assert (
        result["fpr_at_recall"]["0.95"]["labelled"] == 0.0
        and result["fpr_at_recall"]["0.95"]["random"] == 0.0
    )
    assert result["auc"] > 0.99
    reports = tmp_path / "data" / "profiles" / "a" / "reports"
    assert len(list(reports.glob("eval-*.json"))) == 1 and len(list(reports.glob("eval-*.md"))) == 1
    data = json.loads(next(reports.glob("eval-*.json")).read_text())
    assert data["model_id"] == MODEL and data["bands_current"]["positives"]["near_duplicate"] > 0.9
    assert data["distances"]["positives_identical"]["count"] == 10
    assert conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0] == before  # nothing written
    assert IO_HANDLERS["eval"] is run_eval


def test_eval_refuses_without_enough_labelled_positives(world):
    reg, conn, tmp_path = world
    v1 = sqlite3.connect(tmp_path / "v1.db")
    v1.execute("DELETE FROM SimilarFile WHERE IgnoredMode = 1")
    v1.commit()
    v1.close()
    # byte-identical pairs still exist but do not count as labelled positives
    with pytest.raises(EvalError, match="positive"):
        run_eval(ctx_for(reg, {"db_path": str(tmp_path / "v1.db"), "negatives": 10}))
    with pytest.raises(EvalError):
        run_eval(ctx_for(reg, {"db_path": str(tmp_path / "missing.db")}))
