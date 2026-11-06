# Milestone 3 · Similarity engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> This plan was expanded from the task-level plan of the same name after milestone 2 shipped. The "Decide before expanding" items were decided as recorded in [ADR-0005](../decisions/0005-m3-implementation-choices.md) (written in Task 1).

**Goal:** Visually similar still images are found: every image gets a copy-detection vector, vectors live in `vectors.db` and in a per-profile usearch index, a `match` job finds neighbours for new vectors, candidate pairs join the sha1 pairs and the grouping with distance bands, and an evaluation against imported v1 judgments produces the report that locks the model and thresholds.

**Architecture:** The `fingerprint` compute job grows from SHA-1 to SHA-1 + decode (pyvips) + ONNX inference + 256 px webp thumbnail. The ONNX session is loaded lazily once per compute child; model files are downloaded on first use into `/data/models/` with a pinned sha256 (or read from `VISUALSTORE_MODEL_DIR`). Vectors are written by the fingerprint result sink into `vectors.db` (migration 0002: `fingerprint`, `index_key`, `index_meta`). A `match` compute job opens `index.usearch`, adds the batch's vectors under integer keys issued by the server, queries top-k neighbours within `candidate_distance`, and returns raw hits; its result sink aggregates frame hits into file pairs, applies the compare-scope rule, upserts `pair` rows (`source = vector`) and marks files dirty. `rebuild_index` recreates the index from `vectors.db` (the server exports vectors to a temporary `.npy` for the child). Threshold bands are settings; `regroup` already honours `grouping.group_distance`. `eval` is an io job that reads a v1 database read-only, builds labelled pairs, computes cosine distances with numpy from `vectors.db`, and writes a report.

**Tech Stack:** Python 3.12, numpy, onnxruntime (CPU), usearch, pyvips (libvips in the image and in CI), FastAPI, React. Dev: `onnx` to build a tiny test model; `torch` + `torchvision` only for the one-off export script in Task 14 (not a project dependency).

**Spec:** [02 §4](../design/02-architecture.md#4-technology-stack), [03 §5.1 Fingerprint / Pair / Setting, §5.2](../design/03-data-model.md), [04 §6.2 fingerprint / match / rebuild_index / eval, §6.4](../design/04-jobs.md), [05 §7.2, §7.4, §7.5, §7.6, §7.7](../design/05-pipeline.md), [07 §9.5](../design/07-operations.md#95-resources), [11 §13 item 3, §14](../design/11-roadmap.md).

## Global Constraints

Everything in the milestone 1 and 2 plans still holds. In addition:

- Compute children never open a database. They receive paths (media files, model file, index file, exported `.npy`) and return plain data.
- Vectors are L2-normalised float32 in memory and float16 BLOBs on disk (`fingerprint.vector`); `distance` is cosine distance `1 - dot` in `[0, 2]`; sha1 pairs stay at 0.
- One active model per profile: setting `model.id`, default `sscd-resnet50-512-v1`. Vectors, `index_meta` and `pair.model_id` carry the id. Vectors of other models are kept until a later cleanup (design §7.7); `match` only compares within the active model.
- At most one `match` or `rebuild_index` job is in flight per profile (single index writer). The scheduler enforces it.
- Settings: `similarity.candidate_distance = 0.5`, `similarity.near_duplicate = 0.15`, `similarity.variant = 0.3`, `grouping.group_distance = 0.3` (raised from the milestone 2 placeholder 0.2), `similarity.top_k = 64`, `model.id`. Thresholds are provisional until the evaluation (Task 14) writes the measured values back into [00 §1.3](../design/00-overview.md#13-acceptance-criteria).
- Thumbnails: `thumbs/{id[:2]}/{id}.webp`, 256 px on the longest side, from the first frame; never backed up; regenerated when missing.
- Decode failures mark `unreadable`; a file whose content is not media marks `not_media`; a file larger than `media.max_pixels` (default 80 M pixels) marks `unreadable` with a reason. None of these may kill a compute child.
- Model files: `<model_id>.onnx` in `VISUALSTORE_MODEL_DIR` (default `/data/models`), verified by sha256 before first use; download URL and hash come from `visualstore/models/models.json`.
- Compare scope ([05 §7.4 step 4](../design/05-pipeline.md#74-match)) is applied in the match result sink; pairs that fail it are not stored.
- Tests that need libvips or a real model are skipped when the library is absent (`pytest.importorskip("pyvips")`, env flag `VISUALSTORE_TEST_REAL_MODEL`); CI installs `libvips42` and `ffmpeg` so the decode tests run there and in the image.

## Review Focus

1. **A profile whose `index.usearch` is missing, truncated, or built for another `model_id`** must trigger `rebuild_index` at start-up (and after a model switch), and `match` must not run against a stale or missing index meanwhile. (Task 8, Task 11.)
2. **Two files in the same directory of a Folder with `compare_scope = other_directories`** must never form a vector pair; the same two files in different Folders always may; sha1 pairs ignore scope entirely. (Task 7.)
3. **A model switch** must leave judgments and groups intact, queue vectors only for files lacking them for the new model, and never group distances of two models together. (Task 11.)
4. **A renamed zip with a `.jpg` extension, a 1×1 image, a 20000×20000 image header, and a truncated JPEG** must end as `not_media` / `unreadable` / `fingerprinted` as appropriate without killing the compute child or losing the batch. (Task 3, Task 5.)
5. **The eval job** must write nothing to `pair`, `file_group` or judgments, must tolerate v1 SimilarFile rows pointing at missing files, and must refuse to run when fewer than 20 labelled positive pairs have vectors. (Task 10.)

---

## File structure

```
visualstore/
  db/migrations/vectors/0002_fingerprint.sql   fingerprint, index_key, index_meta (Task 1)
  db/fingerprints.py                           frame rows, index keys, meta (Task 1)
  db/settings.py                               + similarity.*, model.id, media.max_pixels (Task 1)
  models/models.json                           model registry data (Task 2; hashes filled in Task 14)
  models/registry.py                           ModelSpec, load_registry, resolve_model_path, ensure_model (Task 2)
  models/infer.py                              Session (lazy ORT), embed(batch) (Task 4)
  media/__init__.py, media/errors.py           NotMedia, Unreadable (Task 3)
  media/decode.py                              load_image, to_model_input, thumbnail_webp (Task 3)
  index/__init__.py, index/usearch_index.py    VectorIndex wrapper (Task 6)
  domain/scope.py                              allowed(folder_a, dir_a, folder_b, dir_b, scope_a, scope_b) (Task 7)
  domain/bands.py                              band(distance, thresholds), BANDS (Task 9)
  jobs/compute/handlers.py                     fingerprint (+decode/infer/thumb), match, rebuild_index (Tasks 5, 7, 8)
  jobs/results.py                              sink_fingerprint (+frames, thumbs, enqueue match), sink_match, sink_rebuild_index (Tasks 5, 7, 8)
  jobs/scheduler.py                            per-profile exclusive compute types (Task 8)
  jobs/io/eval.py                              eval io job + report (Task 10)
  jobs/io/model_switch.py                      model_switch io job (Task 11)
  jobs/startup.py                              index consistency check at start (Task 8)
  api/files.py                                 + /thumbnail (Task 12)
  api/groups.py                                + band on groups, band filter (Task 12)
  api/models.py                                GET /api/models, POST /api/p/{name}/model-switch, eval endpoints (Task 12)
scripts/export_models.py                       one-off torch → ONNX export (Task 14)
web/src/pages/Groups.tsx, GroupDetail.tsx      thumbnails, band column/filter (Task 13)
web/src/pages/Jobs.tsx                         model selector, thresholds, eval trigger, reports (Task 13)
docker/Dockerfile, .github/workflows/ci.yml    media extra in the image; libvips + ffmpeg in CI (Task 13)
tests/unit/test_db_fingerprints.py, test_model_registry.py, test_decode.py, test_infer.py,
tests/unit/test_vector_index.py, test_domain_scope.py, test_domain_bands.py, test_sink_match.py,
tests/integration/test_similarity_pipeline.py, test_eval_job.py, test_model_switch.py, test_api_similarity.py
tests/fixtures/tiny_model.py                   builds a deterministic ONNX model with the onnx package
tests/fixtures/images.py                       generated PNG/JPEG/garbage fixtures (pure Python, no pyvips)
docs/decisions/0005-m3-implementation-choices.md
```

---

### Task 1: Vectors schema, fingerprint queries, similarity settings, ADR-0005

**Files:**
- Create: `visualstore/db/migrations/vectors/0002_fingerprint.sql`, `visualstore/db/fingerprints.py`, `docs/decisions/0005-m3-implementation-choices.md`, `tests/unit/test_db_fingerprints.py`
- Modify: `visualstore/db/settings.py`, `visualstore/jobs/scheduler.py` (`IoContext.open_vectors()`), `tests/unit/test_migrate.py`, `tests/unit/test_settings_db.py`

**Interfaces:**
- Vectors schema: `fingerprint(file_id BLOB, model_id TEXT, frame_index INT, frame_time_ms INT, vector BLOB, PRIMARY KEY (file_id, model_id, frame_index))`; `index_key(seq INTEGER PRIMARY KEY AUTOINCREMENT, file_id BLOB, model_id TEXT, frame_index INT, deleted INT DEFAULT 0, UNIQUE (file_id, model_id, frame_index))`; `index_meta(key TEXT PRIMARY KEY, value TEXT)`.
- `visualstore.db.fingerprints` (every function takes the **vectors** connection `vconn` first; `vector` is `bytes` of float16):
  - `replace_frames(vconn, file_id, model_id, frames: list[tuple[int, int, bytes]]) -> None` (frame_index, time_ms, vector), delete-then-insert in one transaction.
  - `frames_of_file(vconn, file_id, model_id) -> list[Row]`; `vector_count(vconn, model_id) -> int`; `files_with_vectors(vconn, model_id) -> set[bytes]`.
  - `delete_for_files(vconn, file_ids) -> None` removes frames for all models and marks their index keys deleted.
  - `allocate_keys(vconn, file_id, model_id, frame_indices: list[int]) -> list[int]` returns `seq` per frame (existing rows reused); `resolve_keys(vconn, seqs) -> dict[int, tuple[bytes, int]]` (deleted keys excluded); `pending_removals(vconn, model_id) -> list[int]`; `drop_keys(vconn, seqs) -> None`.
  - `get_meta(vconn, key) -> str | None`; `set_meta(vconn, key, value) -> None`.
  - `iter_vectors(vconn, model_id, batch=4096) -> Iterator[list[Row]]` rows of `(seq, file_id, frame_index, vector)` joined with live index keys, for the index export.
- `IoContext.open_vectors() -> sqlite3.Connection` opens `paths.vectors_db` for io handlers.
- Settings added: `model.id = "sscd-resnet50-512-v1"` (str), `similarity.candidate_distance = 0.5`, `similarity.near_duplicate = 0.15`, `similarity.variant = 0.3`, `similarity.top_k = 64` (≥ 1), `media.max_pixels = 80_000_000` (≥ 1); `grouping.group_distance` default becomes `0.3`. String settings must be non-empty.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_db_fingerprints.py`:

```python
import pytest

from visualstore.db import fingerprints as fp
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir

A, B = b"\x01" * 16, b"\x02" * 16
M = "sscd-resnet50-512-v1"


@pytest.fixture
def vconn(tmp_path):
    c = connect(tmp_path / "vectors.db")
    migrate(c, migrations_dir("vectors"))
    return c


def vec(x: int) -> bytes:
    return bytes([x]) * 1024  # 512 float16 values


def test_replace_frames_is_idempotent_and_per_model(vconn):
    fp.replace_frames(vconn, A, M, [(0, 0, vec(1)), (1, 500, vec(2))])
    fp.replace_frames(vconn, A, "other-model", [(0, 0, vec(9))])
    rows = fp.frames_of_file(vconn, A, M)
    assert [(r["frame_index"], r["frame_time_ms"]) for r in rows] == [(0, 0), (1, 500)]
    fp.replace_frames(vconn, A, M, [(0, 0, vec(3))])  # fewer frames: old ones go
    assert [bytes(r["vector"])[:1] for r in fp.frames_of_file(vconn, A, M)] == [b"\x03"]
    assert fp.vector_count(vconn, M) == 1 and fp.vector_count(vconn, "other-model") == 1
    assert fp.files_with_vectors(vconn, M) == {A}


def test_keys_are_stable_and_resolve_back(vconn):
    keys = fp.allocate_keys(vconn, A, M, [0, 1])
    again = fp.allocate_keys(vconn, A, M, [0, 1])
    assert keys == again and len(set(keys)) == 2
    other = fp.allocate_keys(vconn, B, M, [0])
    assert set(other).isdisjoint(keys)
    assert fp.resolve_keys(vconn, keys + other) == {keys[0]: (A, 0), keys[1]: (A, 1), other[0]: (B, 0)}


def test_delete_marks_keys_for_removal_and_drops_frames(vconn):
    fp.replace_frames(vconn, A, M, [(0, 0, vec(1))])
    [k] = fp.allocate_keys(vconn, A, M, [0])
    fp.delete_for_files(vconn, [A])
    assert fp.frames_of_file(vconn, A, M) == []
    assert fp.resolve_keys(vconn, [k]) == {}  # deleted keys never resolve
    assert fp.pending_removals(vconn, M) == [k]
    fp.drop_keys(vconn, [k])
    assert fp.pending_removals(vconn, M) == []
    # a re-added file gets a fresh key, never the recycled one
    [k2] = fp.allocate_keys(vconn, A, M, [0])
    assert k2 != k


def test_meta_and_iteration(vconn):
    assert fp.get_meta(vconn, "model_id") is None
    fp.set_meta(vconn, "model_id", M)
    fp.set_meta(vconn, "model_id", M)  # upsert
    assert fp.get_meta(vconn, "model_id") == M
    for i, fid in enumerate([A, B]):
        fp.replace_frames(vconn, fid, M, [(0, 0, vec(i + 1))])
        fp.allocate_keys(vconn, fid, M, [0])
    fp.replace_frames(vconn, b"\x03" * 16, M, [(0, 0, vec(7))])  # vectors without a key are not exported
    batches = list(fp.iter_vectors(vconn, M, batch=1))
    assert len(batches) == 2 and all(len(b) == 1 for b in batches)
    assert {bytes(r["file_id"]) for b in batches for r in b} == {A, B}
```

Add to `tests/unit/test_migrate.py` parametrisation: `("vectors", {"fingerprint", "index_key", "index_meta"})` replacing the empty set, and the version map `{"global": 1, "profile": 2, "vectors": 2}`. Append to `tests/unit/test_settings_db.py`:

```python
def test_milestone_3_defaults_and_string_setting(conn):
    assert settings.get(conn, "model.id") == "sscd-resnet50-512-v1"
    assert settings.get(conn, "similarity.candidate_distance") == 0.5
    assert settings.get(conn, "similarity.near_duplicate") == 0.15
    assert settings.get(conn, "similarity.variant") == 0.3
    assert settings.get(conn, "similarity.top_k") == 64
    assert settings.get(conn, "grouping.group_distance") == 0.3
    assert settings.get(conn, "media.max_pixels") == 80_000_000
    settings.set(conn, "model.id", "dinov2-vits14-384-v1")
    assert settings.get(conn, "model.id") == "dinov2-vits14-384-v1"
    with pytest.raises(ValueError):
        settings.set(conn, "model.id", "")
    with pytest.raises(ValueError):
        settings.set(conn, "similarity.top_k", 0)
```

Run: `uv run pytest tests/unit/test_db_fingerprints.py tests/unit/test_migrate.py tests/unit/test_settings_db.py -q` — Expected: FAIL (module missing, table set, new keys).

- [ ] **Step 2: Implement**

`visualstore/db/migrations/vectors/0002_fingerprint.sql`:

```sql
-- Milestone 3: per-frame vectors and the usearch key map (design §5.1 Fingerprint, §5.2).
CREATE TABLE fingerprint (
    file_id       BLOB NOT NULL,
    model_id      TEXT NOT NULL,
    frame_index   INTEGER NOT NULL,
    frame_time_ms INTEGER NOT NULL DEFAULT 0,
    vector        BLOB NOT NULL,                 -- float16, dimension set by the model
    PRIMARY KEY (file_id, model_id, frame_index)
);
CREATE INDEX fingerprint_model ON fingerprint (model_id);

-- usearch keys are 64-bit integers; this table is the only map back to (file, frame).
-- Keys are never recycled: a deleted key stays marked until the next match job removes it
-- from the index file, then the row is dropped.
CREATE TABLE index_key (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     BLOB NOT NULL,
    model_id    TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_id, model_id, frame_index)
);
CREATE INDEX index_key_deleted ON index_key (model_id, deleted);

CREATE TABLE index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

`visualstore/db/fingerprints.py`:

```python
"""Vector storage and the usearch key map, all in vectors.db (design §5.1 Fingerprint, §5.2)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator


def replace_frames(
    vconn: sqlite3.Connection, file_id: bytes, model_id: str, frames: list[tuple[int, int, bytes]]
) -> None:
    with vconn:
        vconn.execute(
            "DELETE FROM fingerprint WHERE file_id = ? AND model_id = ?", (file_id, model_id)
        )
        vconn.executemany(
            "INSERT INTO fingerprint (file_id, model_id, frame_index, frame_time_ms, vector)"
            " VALUES (?, ?, ?, ?, ?)",
            [(file_id, model_id, idx, ms, vec) for idx, ms, vec in frames],
        )


def frames_of_file(vconn: sqlite3.Connection, file_id: bytes, model_id: str) -> list[sqlite3.Row]:
    return vconn.execute(
        "SELECT * FROM fingerprint WHERE file_id = ? AND model_id = ? ORDER BY frame_index",
        (file_id, model_id),
    ).fetchall()


def vector_count(vconn: sqlite3.Connection, model_id: str) -> int:
    return vconn.execute(
        "SELECT COUNT(*) FROM fingerprint WHERE model_id = ?", (model_id,)
    ).fetchone()[0]


def files_with_vectors(vconn: sqlite3.Connection, model_id: str) -> set[bytes]:
    rows = vconn.execute(
        "SELECT DISTINCT file_id FROM fingerprint WHERE model_id = ?", (model_id,)
    ).fetchall()
    return {bytes(r["file_id"]) for r in rows}


def delete_for_files(vconn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    ids = [(f,) for f in file_ids]
    with vconn:
        vconn.executemany("DELETE FROM fingerprint WHERE file_id = ?", ids)
        vconn.executemany("UPDATE index_key SET deleted = 1 WHERE file_id = ?", ids)


def allocate_keys(
    vconn: sqlite3.Connection, file_id: bytes, model_id: str, frame_indices: list[int]
) -> list[int]:
    with vconn:
        vconn.executemany(
            "INSERT OR IGNORE INTO index_key (file_id, model_id, frame_index) VALUES (?, ?, ?)",
            [(file_id, model_id, i) for i in frame_indices],
        )
        vconn.executemany(
            "UPDATE index_key SET deleted = 0 WHERE file_id = ? AND model_id = ? AND frame_index = ?",
            [(file_id, model_id, i) for i in frame_indices],
        )
    rows = vconn.execute(
        "SELECT seq, frame_index FROM index_key WHERE file_id = ? AND model_id = ?",
        (file_id, model_id),
    ).fetchall()
    by_frame = {r["frame_index"]: r["seq"] for r in rows}
    return [by_frame[i] for i in frame_indices]


def resolve_keys(vconn: sqlite3.Connection, seqs: Iterable[int]) -> dict[int, tuple[bytes, int]]:
    seqs = list(seqs)
    if not seqs:
        return {}
    out: dict[int, tuple[bytes, int]] = {}
    for start in range(0, len(seqs), 900):  # stay under SQLite's variable limit
        chunk = seqs[start : start + 900]
        marks = ",".join("?" * len(chunk))
        for r in vconn.execute(
            f"SELECT seq, file_id, frame_index FROM index_key WHERE deleted = 0 AND seq IN ({marks})",
            chunk,
        ):
            out[r["seq"]] = (bytes(r["file_id"]), r["frame_index"])
    return out


def pending_removals(vconn: sqlite3.Connection, model_id: str) -> list[int]:
    rows = vconn.execute(
        "SELECT seq FROM index_key WHERE deleted = 1 AND model_id = ? ORDER BY seq", (model_id,)
    ).fetchall()
    return [r["seq"] for r in rows]


def drop_keys(vconn: sqlite3.Connection, seqs: Iterable[int]) -> None:
    with vconn:
        vconn.executemany("DELETE FROM index_key WHERE seq = ?", [(s,) for s in seqs])


def get_meta(vconn: sqlite3.Connection, key: str) -> str | None:
    row = vconn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(vconn: sqlite3.Connection, key: str, value: str) -> None:
    with vconn:
        vconn.execute(
            "INSERT INTO index_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def iter_vectors(
    vconn: sqlite3.Connection, model_id: str, batch: int = 4096
) -> Iterator[list[sqlite3.Row]]:
    """Live (key, vector) rows for one model, in key order, for building the index."""
    last = -1
    while True:
        rows = vconn.execute(
            "SELECT k.seq, k.file_id, k.frame_index, f.vector FROM index_key k"
            " JOIN fingerprint f ON f.file_id = k.file_id AND f.model_id = k.model_id"
            " AND f.frame_index = k.frame_index"
            " WHERE k.model_id = ? AND k.deleted = 0 AND k.seq > ? ORDER BY k.seq LIMIT ?",
            (model_id, last, batch),
        ).fetchall()
        if not rows:
            return
        yield rows
        last = rows[-1]["seq"]
```

`visualstore/db/settings.py`: add to `DEFAULTS`:

```python
    "model.id": "sscd-resnet50-512-v1",
    "similarity.candidate_distance": 0.5,
    "similarity.near_duplicate": 0.15,
    "similarity.variant": 0.3,
    "similarity.top_k": 64,
    "media.max_pixels": 80_000_000,
```

change `"grouping.group_distance": 0.2` to `0.3`, add `"similarity.top_k"` and `"media.max_pixels"` to `_AT_LEAST_ONE`, and in `set` add a string rule before the numeric check: `if isinstance(value, str) and not value.strip(): raise ValueError(f"setting {key!r} must not be empty")`.

`visualstore/jobs/scheduler.py`: next to `open_metadata`, add

```python
    def open_vectors(self) -> sqlite3.Connection:
        from visualstore.db.connection import connect

        return connect(self.paths.vectors_db)
```

- [ ] **Step 3: Write ADR-0005**

`docs/decisions/0005-m3-implementation-choices.md`:

```markdown
# ADR-0005 · Milestone 3 implementation choices

Date: 2026-11-06
Status: accepted

## Context

The milestone 3 task-level plan listed decisions to take before expansion. They are taken here, together with facts learned while probing the toolchain.

## Decisions

- **Model artefacts are downloaded on first use** into `VISUALSTORE_MODEL_DIR` (default `/data/models`) and verified against a sha256 pinned in `visualstore/models/models.json`. The image does not embed weights. An entry whose sha256 is empty is "local only": the server refuses to download it and accepts only a file already present in the model directory. ONNX files are produced once by `scripts/export_models.py` (torch + torchvision, not project dependencies) and published as assets of a GitHub release of this repository; the registry points at those URLs.
- **usearch keys are plain `index_key.seq` integers** issued by the server (never recycled). The task-level plan proposed encoding a directory hash into the key so the child could filter same-directory neighbours during the search; usearch's Python filtered-search API is not relied upon. Instead the child over-fetches (`similarity.top_k`, default 64, as the design's fallback) and the result sink applies compare scope. If same-directory crowding shows up in practice, raise `top_k` or revisit filtered search in milestone 6.
- **Compare scope is applied in the match result sink**, server side, exactly as design §7.4 step 4 states; pairs that fail it are not stored.
- **Thresholds are provisional** (`candidate 0.5`, `near_duplicate 0.15`, `variant 0.3`, `group_distance 0.3`) until the evaluation report; the measured values go into design §1.3.
- **Candidate models**: SSCD ResNet-50 (512-d, 288 px input) first; DINOv2 ViT-S/14 (384-d, 224 px) as the comparison. Both exported to ONNX with dynamic batch size.
- **`eval` is an io job**, not a compute job as §6.2 lists: it reads the v1 database and `vectors.db` and does a few hundred thousand dot products with numpy, well under a minute; running it in a child would only add an export step.
- **Index removals are deferred**: deleting a file marks its `index_key` rows `deleted`; the next `match` job removes those keys from the index file and the sink drops the rows. Hits on not-yet-removed keys resolve to nothing and are ignored.
- **Decode tests need libvips**: they `importorskip("pyvips")`. The development machine used for milestone 3 has no libvips, so these tests run in CI (which installs `libvips42` and `ffmpeg`) and inside the image. Inference tests use a tiny ONNX model built with the `onnx` package; the real model is exercised only when `VISUALSTORE_TEST_REAL_MODEL=1`.
- **Frames for deleted files** are removed explicitly by the scan and trash paths through `IoContext.open_vectors()` (no cross-database foreign keys); milestone 6's maintenance job sweeps orphans.

## Consequences

- Milestone 5 adds frames per file without schema changes.
- A model switch is a setting change plus a `model_switch` job; old vectors stay until cleanup.
- Anyone running without internet access mounts a model directory and sets `VISUALSTORE_MODEL_DIR`.
```

Add `| [0005](0005-m3-implementation-choices.md) | Milestone 3 implementation choices: model download, plain usearch keys, scope in the sink, eval as io | accepted |` to `docs/decisions/README.md`.

- [ ] **Step 4: Run the tests, then commit**

Run: `uv run pytest tests/unit -q` — Expected: all passed.

```bash
git add visualstore/db/migrations/vectors/0002_fingerprint.sql visualstore/db/fingerprints.py visualstore/db/settings.py visualstore/jobs/scheduler.py docs/decisions tests/unit/test_db_fingerprints.py tests/unit/test_migrate.py tests/unit/test_settings_db.py
git commit -m "feat: vectors schema, fingerprint queries and similarity settings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Model registry and download

**Files:**
- Create: `visualstore/models/__init__.py`, `visualstore/models/models.json`, `visualstore/models/registry.py`, `tests/unit/test_model_registry.py`
- Modify: `visualstore/config.py` (`model_dir`), `tests/unit/test_config.py`

**Interfaces:**
- `Config.model_dir: Path` from `VISUALSTORE_MODEL_DIR` (default `/data/models`).
- `visualstore.models.registry`: `ModelError(Exception)`; `ModelSpec(id, url, sha256, dim, input_size, mean: tuple[float, float, float], std: tuple[float, float, float], description)`; `load_registry() -> dict[str, ModelSpec]` from `models.json`; `get_spec(model_id) -> ModelSpec` (ModelError for unknown); `resolve_model_path(spec, model_dir) -> Path` — returns `<model_dir>/<id>.onnx` when present and (if `spec.sha256`) its hash matches, otherwise downloads `spec.url` to a temp file, verifies, renames; empty `sha256` + missing file → `ModelError("not downloadable")`; hash mismatch on an existing file → `ModelError` (file left in place for the operator); `sha256_of(path) -> str`.
- `models.json` ships both candidate models with their URLs; sha256 values are filled by Task 14 once the artefacts exist (until then they are empty, which the registry treats as "local only").

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_model_registry.py`:

```python
import hashlib

import pytest

from visualstore.models import registry
from visualstore.models.registry import ModelError, ModelSpec


def spec(tmp_path, data=b"onnx-bytes", sha="auto", url=None):
    src = tmp_path / "src.onnx"
    src.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest() if sha == "auto" else sha
    return ModelSpec(
        id="tiny-test-8-v1", url=url or src.as_uri(), sha256=digest, dim=8, input_size=32,
        mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5), description="test",
    )


def test_registry_ships_both_candidates():
    reg = registry.load_registry()
    assert {"sscd-resnet50-512-v1", "dinov2-vits14-384-v1"} <= set(reg)
    s = registry.get_spec("sscd-resnet50-512-v1")
    assert s.dim == 512 and s.input_size == 288 and s.url.startswith("https://")
    with pytest.raises(ModelError):
        registry.get_spec("nope")


def test_download_verifies_and_caches(tmp_path):
    s = spec(tmp_path)
    model_dir = tmp_path / "models"
    path = registry.resolve_model_path(s, model_dir)
    assert path == model_dir / "tiny-test-8-v1.onnx" and path.read_bytes() == b"onnx-bytes"
    assert not list(model_dir.glob("*.part"))
    (tmp_path / "src.onnx").unlink()  # second resolve must not touch the network
    assert registry.resolve_model_path(s, model_dir) == path


def test_bad_download_is_rejected_and_removed(tmp_path):
    s = spec(tmp_path, sha="0" * 64)
    with pytest.raises(ModelError, match="sha256"):
        registry.resolve_model_path(s, tmp_path / "models")
    assert not (tmp_path / "models" / "tiny-test-8-v1.onnx").exists()


def test_existing_file_with_wrong_hash_is_refused_but_kept(tmp_path):
    s = spec(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "tiny-test-8-v1.onnx").write_bytes(b"tampered")
    with pytest.raises(ModelError, match="sha256"):
        registry.resolve_model_path(s, model_dir)
    assert (model_dir / "tiny-test-8-v1.onnx").read_bytes() == b"tampered"


def test_local_only_when_hash_is_unknown(tmp_path):
    s = spec(tmp_path, sha="")
    with pytest.raises(ModelError, match="not downloadable"):
        registry.resolve_model_path(s, tmp_path / "models")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "tiny-test-8-v1.onnx").write_bytes(b"whatever")
    assert registry.resolve_model_path(s, model_dir).read_bytes() == b"whatever"
```

Append to `tests/unit/test_config.py`:

```python
def test_model_dir_default_and_override():
    assert Config.from_env({"VISUALSTORE_PASSWORD": "pw"}).model_dir == Path("/data/models")
    cfg = Config.from_env({"VISUALSTORE_PASSWORD": "pw", "VISUALSTORE_MODEL_DIR": "/models"})
    assert cfg.model_dir == Path("/models")
```

Run: `uv run pytest tests/unit/test_model_registry.py tests/unit/test_config.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/config.py`: add field `model_dir: Path` and `model_dir=Path(env.get("VISUALSTORE_MODEL_DIR") or "/data/models")`. Update the integration `conftest.py` `Config(...)` call to pass `model_dir=tmp_path / "models"` and `tests/unit/test_scheduler.py`/others that construct `Config` directly if any.

`visualstore/models/__init__.py`: empty. `visualstore/models/models.json`:

```json
{
  "sscd-resnet50-512-v1": {
    "url": "https://github.com/SecretNest/VisualStore/releases/download/models-v1/sscd-resnet50-512-v1.onnx",
    "sha256": "",
    "dim": 512,
    "input_size": 288,
    "mean": [0.485, 0.456, 0.406],
    "std": [0.229, 0.224, 0.225],
    "description": "SSCD (Meta, self-supervised copy detection), ResNet-50 disc_mixup, 512-d, exported to ONNX"
  },
  "dinov2-vits14-384-v1": {
    "url": "https://github.com/SecretNest/VisualStore/releases/download/models-v1/dinov2-vits14-384-v1.onnx",
    "sha256": "",
    "dim": 384,
    "input_size": 224,
    "mean": [0.485, 0.456, 0.406],
    "std": [0.229, 0.224, 0.225],
    "description": "DINOv2 ViT-S/14 CLS embedding, 384-d, exported to ONNX (comparison model)"
  }
}
```

`visualstore/models/registry.py`:

```python
"""Model registry and artefact resolution (ADR-0005)."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_FILE = Path(__file__).parent / "models.json"


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    url: str
    sha256: str
    dim: int
    input_size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    description: str

    def file_name(self) -> str:
        return f"{self.id}.onnx"


def load_registry() -> dict[str, ModelSpec]:
    raw = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
    return {
        model_id: ModelSpec(
            id=model_id, url=v["url"], sha256=v.get("sha256", ""), dim=int(v["dim"]),
            input_size=int(v["input_size"]), mean=tuple(v["mean"]), std=tuple(v["std"]),
            description=v.get("description", ""),
        )
        for model_id, v in raw.items()
    }


def get_spec(model_id: str) -> ModelSpec:
    try:
        return load_registry()[model_id]
    except KeyError:
        raise ModelError(f"unknown model id {model_id!r}") from None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model_path(spec: ModelSpec, model_dir: Path) -> Path:
    """The verified local file for a model, downloading it when allowed and absent."""
    target = model_dir / spec.file_name()
    if target.is_file():
        if spec.sha256 and sha256_of(target) != spec.sha256:
            raise ModelError(
                f"{target} does not match the registered sha256 for {spec.id}; replace or remove it"
            )
        return target
    if not spec.sha256:
        raise ModelError(
            f"model {spec.id} has no registered sha256 and is therefore not downloadable;"
            f" place {spec.file_name()} in {model_dir} by hand"
        )
    model_dir.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(spec.url, timeout=60) as resp, open(part, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        if sha256_of(part) != spec.sha256:
            raise ModelError(f"downloaded {spec.id} failed the sha256 check; download discarded")
        part.replace(target)
    except ModelError:
        part.unlink(missing_ok=True)
        raise
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise ModelError(f"could not download {spec.id} from {spec.url}: {exc}") from exc
    return target
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_model_registry.py tests/unit/test_config.py tests/integration/test_api_auth.py -q` — Expected: all passed.

```bash
git add visualstore/models visualstore/config.py tests/unit/test_model_registry.py tests/unit/test_config.py tests/integration/conftest.py
git commit -m "feat: model registry with verified download and VISUALSTORE_MODEL_DIR

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Image decoding and thumbnails (pyvips)

**Files:**
- Create: `visualstore/media/__init__.py`, `visualstore/media/errors.py`, `visualstore/media/decode.py`, `tests/fixtures/images.py`, `tests/unit/test_decode.py`
- Modify: `pyproject.toml` (`pyvips`, `numpy` become core dependencies; `onnxruntime`, `usearch` too — one dependency change for the milestone), `.github/workflows/ci.yml` (apt install `libvips42 ffmpeg` in the backend job)

**Interfaces:**
- `media.errors`: `NotMedia(Exception)` (content is not an image), `Unreadable(Exception)` (decode failed, too large, truncated).
- `media.decode`: `Decoded(image: pyvips.Image, width: int, height: int)`; `load_image(path: str, max_pixels: int) -> Decoded` (EXIF-rotated, sRGB, alpha flattened on white); `to_model_input(decoded, size: int, mean, std) -> np.ndarray` float32 `(1, 3, size, size)`, shorter side scaled to `size`, centre crop; `thumbnail_webp(decoded, longest: int = 256) -> bytes`.
- `tests/fixtures/images.py`: pure-Python generators: `png_bytes(width, height, rgb=(r,g,b), gradient=False) -> bytes`, `png_header_only(width, height) -> bytes` (valid signature + IHDR, no IDAT), `truncated(data: bytes) -> bytes` (first half), `garbage() -> bytes`.

- [ ] **Step 1: Write the fixture and the failing tests**

`tests/fixtures/images.py`:

```python
"""Image fixtures without any imaging library: PNG is a zlib stream plus CRC'd chunks."""

import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png_header_only(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)


def png_bytes(width: int, height: int, rgb=(200, 30, 30), gradient: bool = False) -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type none
        for x in range(width):
            if gradient:
                rows.extend((x * 255 // max(width - 1, 1), y * 255 // max(height - 1, 1), 128))
            else:
                rows.extend(rgb)
    idat = zlib.compress(bytes(rows), 6)
    return png_header_only(width, height) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def garbage() -> bytes:
    return b"PK\x03\x04 this is really a zip archive, not a picture " * 20
```

`tests/unit/test_decode.py`:

```python
import numpy as np
import pytest

from tests.fixtures.images import garbage, png_bytes, png_header_only, truncated

pyvips = pytest.importorskip("pyvips")

from visualstore.media.decode import load_image, thumbnail_webp, to_model_input  # noqa: E402
from visualstore.media.errors import NotMedia, Unreadable  # noqa: E402

MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_decode_and_model_input_shape_and_normalisation(tmp_path):
    d = load_image(write(tmp_path, "g.png", png_bytes(64, 40, gradient=True)), max_pixels=10_000_000)
    assert (d.width, d.height) == (64, 40)
    x = to_model_input(d, 32, MEAN, STD)
    assert x.shape == (1, 3, 32, 32) and x.dtype == np.float32
    assert -3 < x.min() < x.max() < 3  # normalised, not raw 0..255


def test_solid_colour_survives_resize(tmp_path):
    d = load_image(write(tmp_path, "r.png", png_bytes(50, 50, rgb=(255, 0, 0))), max_pixels=10_000_000)
    x = to_model_input(d, 16, (0, 0, 0), (1, 1, 1))  # identity normalisation: values in 0..1
    assert np.allclose(x[0, 0], 1.0, atol=0.02) and np.allclose(x[0, 1], 0.0, atol=0.02)


def test_thumbnail_is_webp_and_bounded(tmp_path):
    d = load_image(write(tmp_path, "big.png", png_bytes(300, 120)), max_pixels=10_000_000)
    data = thumbnail_webp(d, 64)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    thumb = pyvips.Image.new_from_buffer(data, "")
    assert max(thumb.width, thumb.height) == 64


def test_garbage_with_image_extension_is_not_media(tmp_path):
    """Review Focus 4."""
    with pytest.raises(NotMedia):
        load_image(write(tmp_path, "renamed.jpg", garbage()), max_pixels=10_000_000)


def test_truncated_and_oversized_are_unreadable(tmp_path):
    """Review Focus 4."""
    with pytest.raises(Unreadable):
        load_image(write(tmp_path, "cut.png", truncated(png_bytes(200, 200))), max_pixels=10_000_000)
    with pytest.raises(Unreadable, match="pixels"):
        load_image(write(tmp_path, "huge.png", png_header_only(20000, 20000)), max_pixels=1_000_000)


def test_one_by_one_image_decodes(tmp_path):
    d = load_image(write(tmp_path, "dot.png", png_bytes(1, 1)), max_pixels=10)
    assert to_model_input(d, 8, MEAN, STD).shape == (1, 3, 8, 8)
```

Run: `uv run pytest tests/unit/test_decode.py -q` — Expected: on the development machine `1 skipped` (no libvips) — **the failure must be observed where libvips exists**: run `docker build -f docker/Dockerfile -t visualstore:dev . && docker run --rm -v "$PWD/tests:/app/tests" visualstore:dev python -m pytest /app/tests/unit/test_decode.py -q` after the image includes pytest (add `RUN uv sync --frozen --extra media` without `--no-dev` in a `test` build stage, see Step 2) — Expected: FAIL with `ModuleNotFoundError: visualstore.media`.

- [ ] **Step 2: Dependencies, CI and image**

`pyproject.toml` `dependencies` gains `"numpy>=1.26"`, `"onnxruntime>=1.17"`, `"usearch>=2.9"`, `"pyvips>=2.2"`; the `media` extra is removed (everything is core now) and the Dockerfile's `--extra media` flags go away. Add a `test` stage to `docker/Dockerfile` after `runtime`:

```dockerfile
# ---- test stage: the runtime image plus dev dependencies, for running the suite with libvips ----
FROM runtime AS test
RUN uv sync --frozen --no-editable
```

(`docker build --target test -t visualstore:test .` builds it; the default target stays `runtime` because it is the last stage — move the `test` stage **before** the final `runtime` `ENV`/`CMD` block or put `FROM runtime AS test` at the very end and build the publish image with `--target runtime`; choose the latter and set `target: runtime` in both workflows.)

`.github/workflows/ci.yml` backend job: insert before "Install":

```yaml
      - name: System libraries for media tests
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends libvips42 ffmpeg
```

Run `uv lock && uv sync` after editing `pyproject.toml`.

- [ ] **Step 3: Implement**

`visualstore/media/__init__.py`: empty. `visualstore/media/errors.py`:

```python
class NotMedia(Exception):
    """The file's content is not an image (or video) despite its extension."""


class Unreadable(Exception):
    """The file is media but cannot be decoded: truncated, corrupt, too large."""
```

`visualstore/media/decode.py`:

```python
"""Still-image decoding with libvips (design §7.2). Frames for animated/video arrive in M5."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyvips

from visualstore.media.errors import NotMedia, Unreadable

_NOT_MEDIA_MARKERS = ("not a known file format", "is not in a known format", "no loader")


@dataclass
class Decoded:
    image: pyvips.Image
    width: int
    height: int


def load_image(path: str, max_pixels: int) -> Decoded:
    try:
        img = pyvips.Image.new_from_file(path, access="sequential")
    except pyvips.Error as exc:
        message = str(exc)
        if any(marker in message for marker in _NOT_MEDIA_MARKERS):
            raise NotMedia(message) from exc
        raise Unreadable(message) from exc
    if img.width * img.height > max_pixels:
        raise Unreadable(f"{img.width}x{img.height} exceeds the limit of {max_pixels} pixels")
    try:
        img = img.autorot()
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        if img.interpretation != "srgb" or img.bands != 3:
            img = img.colourspace("srgb")
        if img.bands > 3:
            img = img.extract_band(0, n=3)
        img = img.copy_memory()  # decode now so truncated data fails here, not later
    except pyvips.Error as exc:
        raise Unreadable(str(exc)) from exc
    return Decoded(image=img, width=img.width, height=img.height)


def to_model_input(decoded: Decoded, size: int, mean, std) -> np.ndarray:
    """Shorter side to `size`, centre crop, CHW float32 normalised, batch dimension 1."""
    img = decoded.image.thumbnail_image(size, height=size, size="both", crop="centre")
    if img.width != size or img.height != size:  # tiny inputs: force the exact shape
        img = img.resize(size / img.width, vscale=size / img.height)
    arr = np.ndarray(buffer=img.write_to_memory(), dtype=np.uint8, shape=[img.height, img.width, img.bands])
    arr = arr[:, :, :3].astype(np.float32) / 255.0
    arr = (arr - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None, ...])


def thumbnail_webp(decoded: Decoded, longest: int = 256) -> bytes:
    scale = longest / max(decoded.width, decoded.height)
    img = decoded.image if scale >= 1 else decoded.image.resize(scale)
    return img.write_to_buffer(".webp[Q=80]")
```

- [ ] **Step 4: Run the tests where libvips exists, then commit**

Run: `docker build --target test -t visualstore:test . && docker run --rm -v "$PWD/tests:/app/tests" visualstore:test python -m pytest /app/tests/unit/test_decode.py -q` — Expected: 6 passed. Locally: `uv run pytest tests/unit/test_decode.py -q` — Expected: `1 skipped`.

```bash
git add pyproject.toml uv.lock visualstore/media tests/fixtures/images.py tests/unit/test_decode.py docker/Dockerfile .github/workflows/ci.yml .github/workflows/docker-publish.yml
git commit -m "feat: libvips image decoding, model input preparation and webp thumbnails

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: ONNX inference session

**Files:**
- Create: `visualstore/models/infer.py`, `tests/fixtures/tiny_model.py`, `tests/unit/test_infer.py`
- Modify: `pyproject.toml` dev group (`onnx`)

**Interfaces:**
- `models.infer.Session(model_path: Path, spec: ModelSpec)` with lazy `onnxruntime.InferenceSession` creation (1 intra-op thread, CPU provider), `.embed(batch: np.ndarray) -> np.ndarray` float32 `(N, spec.dim)` L2-normalised, raising `ModelError` when the model's output dimension differs from `spec.dim`; `.input_name`.
- `tests/fixtures/tiny_model.build_tiny_model(path: Path, dim: int = 8) -> None` writes a deterministic ONNX graph: `input[N,3,H,W] → ReduceMean(axes 2,3) → (N,3) → MatMul(const 3×dim) → output[N,dim]`, and `tiny_spec(dim=8) -> ModelSpec` (`input_size=16`, identity normalisation).

- [ ] **Step 1: Write the fixture and the failing tests**

`tests/fixtures/tiny_model.py`:

```python
"""A deterministic ONNX model small enough to build in a test: colour means → linear map."""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from visualstore.models.registry import ModelSpec


def build_tiny_model(path: Path, dim: int = 8) -> None:
    rng = np.random.default_rng(42)
    weights = numpy_helper.from_array(rng.normal(size=(3, dim)).astype(np.float32), name="W")
    inp = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, "H", "W"])
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", dim])
    nodes = [
        helper.make_node("ReduceMean", ["input"], ["means"], axes=[2, 3], keepdims=0),
        helper.make_node("MatMul", ["means", "W"], ["output"]),
    ]
    graph = helper.make_graph(nodes, "tiny", [inp], [out], initializer=[weights])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def tiny_spec(dim: int = 8) -> ModelSpec:
    return ModelSpec(
        id=f"tiny-test-{dim}-v1", url="", sha256="", dim=dim, input_size=16,
        mean=(0.0, 0.0, 0.0), std=(1.0, 1.0, 1.0), description="test model",
    )
```

`tests/unit/test_infer.py`:

```python
import numpy as np
import pytest

from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from visualstore.models.infer import Session
from visualstore.models.registry import ModelError


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "tiny.onnx"
    build_tiny_model(path, dim=8)
    return path


def test_embed_returns_unit_vectors_per_row(model):
    session = Session(model, tiny_spec(8))
    batch = np.random.default_rng(1).random((3, 3, 16, 16), dtype=np.float32)
    out = session.embed(batch)
    assert out.shape == (3, 8) and out.dtype == np.float32
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    single = session.embed(batch[:1])
    assert np.allclose(single[0], out[0], atol=1e-5)  # batch size does not change results


def test_same_colour_means_give_same_vector(model):
    session = Session(model, tiny_spec(8))
    a = np.full((1, 3, 16, 16), 0.3, dtype=np.float32)
    b = np.full((1, 3, 32, 32), 0.3, dtype=np.float32)  # different size, same means
    assert np.allclose(session.embed(a), session.embed(b), atol=1e-5)


def test_dimension_mismatch_is_a_model_error(model):
    with pytest.raises(ModelError, match="dimension"):
        Session(model, tiny_spec(16)).embed(np.zeros((1, 3, 16, 16), dtype=np.float32))


def test_session_is_created_lazily(model):
    session = Session(model, tiny_spec(8))
    assert session._session is None
    session.embed(np.zeros((1, 3, 16, 16), dtype=np.float32))
    assert session._session is not None
```

Run: `uv add --group dev onnx` then `uv run pytest tests/unit/test_infer.py -q` — Expected: FAIL (`visualstore.models.infer` missing).

- [ ] **Step 2: Implement**

`visualstore/models/infer.py`:

```python
"""ONNX Runtime inference, one lazily created session per process (design §4, §7.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from visualstore.models.registry import ModelError, ModelSpec


class Session:
    def __init__(self, model_path: Path, spec: ModelSpec) -> None:
        self.model_path = Path(model_path)
        self.spec = spec
        self._session = None
        self.input_name: str | None = None

    def _ensure(self):
        if self._session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # children run in parallel; one thread each
            options.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.input_name = self._session.get_inputs()[0].name
        return self._session

    def embed(self, batch: np.ndarray) -> np.ndarray:
        session = self._ensure()
        out = session.run(None, {self.input_name: np.ascontiguousarray(batch, dtype=np.float32)})[0]
        out = np.asarray(out, dtype=np.float32).reshape(batch.shape[0], -1)
        if out.shape[1] != self.spec.dim:
            raise ModelError(
                f"model {self.spec.id} produced dimension {out.shape[1]}, registry says {self.spec.dim}"
            )
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_infer.py tests/unit/test_model_registry.py -q` — Expected: all passed.

```bash
git add pyproject.toml uv.lock visualstore/models/infer.py tests/fixtures/tiny_model.py tests/unit/test_infer.py
git commit -m "feat: lazy ONNX Runtime session with normalised embeddings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Fingerprint handler with vectors and thumbnails; sink context refactor

**Files:**
- Modify: `visualstore/jobs/compute/handlers.py`, `visualstore/jobs/results.py`, `visualstore/jobs/scheduler.py`, `visualstore/jobs/io/scan.py`, `visualstore/jobs/io/actions.py`, `visualstore/jobs/io/import_v1.py`, `visualstore/app.py`, `visualstore/models/registry.py` (`job_params`), `tests/unit/test_sink_fingerprint.py`, `tests/unit/test_result_sinks.py`, `tests/unit/test_fingerprint_handler.py`, `tests/integration/test_scan_job.py`
- Create: `tests/unit/test_fingerprint_vectors.py`

**Interfaces:**
- **Sink signature changes** to `sink(ctx: SinkContext)` with `SinkContext(conn, vconn, registry, profile, paths, settings: dict, model_dir: Path, params: dict, result: dict)`; the scheduler builds it (it already holds the registry). `RESULT_SINKS: dict[str, Callable[[SinkContext], None]]`. Existing tests move to the new signature through a helper `make_sink_ctx(registry, profile, params, result)` in `tests/unit/sinkctx.py`.
- `IoContext.model_dir: Path` (from `Config.model_dir`; the scheduler gains a `model_dir` argument).
- `registry.job_params(spec, model_dir) -> dict`: `{"id", "path": str(model_dir / file), "dim", "input_size", "mean", "std"}` — plain data for children.
- Fingerprint job params gain `"model": job_params(...)`, `"max_pixels": int`, `"priority": int`. Scan (`_enqueue_fingerprints`) fills them from `ctx.settings["model.id"]`, `ctx.settings["media.max_pixels"]` and `ctx.model_dir`.
- Handler `fingerprint`: per entry with `media_type == "image"` and a `model` in params: sha1 + decode + embed + thumbnail; result entry gains `"frames": [{"index": 0, "time_ms": 0, "vector": <base64 of float16>}]`, `"thumbnail": <base64 webp>`, `"model_id"`. `NotMedia` → `state = "not_media"` (sha1 kept), `Unreadable` → `"unreadable"` (sha1 kept), a missing model file → the whole job raises `ModelError` (job fails visibly). Other media types: sha1 only (milestone 5). A module-level cache keeps one `Session` per model path per process.
- `sink_fingerprint`: as before for sha1 and states; additionally for entries with `frames`: `fingerprints.replace_frames` + `allocate_keys` in `vconn`, thumbnail written to `paths.thumbs/{id[:2]}/{id}.webp`, and a `match` job enqueued with params `{"model", "index_path": str(paths.index), "entries": [{"seq", "vector"}], "remove": pending_removals, "top_k", "candidate_distance", "priority"}` at the fingerprint job's priority. Stale results are still ignored by size/mtime.
- Deletions remove vectors: `scan` (deleted and reset files) and `apply_action` trash call `fingerprints.delete_for_files` on `ctx.open_vectors()`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/sinkctx.py`:

```python
from pathlib import Path

from visualstore.db import settings as settings_db
from visualstore.jobs.results import SinkContext


def make_sink_ctx(registry, profile: str, params: dict, result: dict, model_dir: Path | None = None) -> SinkContext:
    conn = registry.conn(profile)
    return SinkContext(
        conn=conn, vconn=registry.vectors_conn(profile), registry=registry, profile=profile,
        paths=registry.paths(profile), settings=settings_db.all(conn),
        model_dir=model_dir or registry.data_dir / "models", params=params, result=result,
    )
```

Rewrite `tests/unit/test_sink_fingerprint.py` so the fixture uses a `ProfileRegistry` (profile `a`) instead of a bare connection and every call becomes `sink_fingerprint(make_sink_ctx(reg, "a", {}, {...}))`; assertions unchanged. In `tests/unit/test_result_sinks.py` the monkeypatched sinks become `lambda ctx: seen.append((ctx.profile, ctx.params, ctx.result))` and `def explode(ctx): raise ...`.

`tests/unit/test_fingerprint_vectors.py`:

```python
import base64

import numpy as np
import pytest

from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, fingerprints, folders
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.jobs.compute.handlers import fingerprint
from visualstore.jobs.results import sink_fingerprint
from visualstore.models.registry import ModelError, job_params
from visualstore.profiles import ProfileRegistry

pyvips = pytest.importorskip("pyvips")


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)

    def add(name, data, media="jpg"):
        (lib / name).write_bytes(data)
        st = (lib / name).stat()
        return files.insert_file(conn, folder_id=folder, directory="", name=name, extension_id=ext[media]["id"], size=st.st_size, mtime=st.st_mtime_ns)

    yield reg, conn, lib, model_dir, spec, add
    reg.close()


def entry(fid, lib, name, media_type="image"):
    return {"id": id_to_str(fid), "path": str(lib / name), "media_type": media_type}


def test_handler_adds_vectors_thumbnails_and_states(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(40, 30, gradient=True))
    bad = add("zip.jpg", garbage())
    other = add("doc.mp4", b"not decoded in milestone 3", media="mp4")
    result = fingerprint(
        {"files": [entry(good, lib, "good.png"), entry(bad, lib, "zip.jpg"), entry(other, lib, "doc.mp4", "video")],
         "model": job_params(spec, model_dir), "max_pixels": 10_000_000},
        lambda p: None,
    )
    by_id = {e["id"]: e for e in result["files"]}
    g = by_id[id_to_str(good)]
    assert g["state"] == "fingerprinted" and g["model_id"] == spec.id and len(g["frames"]) == 1
    vec = np.frombuffer(base64.b64decode(g["frames"][0]["vector"]), dtype=np.float16)
    assert vec.shape == (8,) and abs(float(np.linalg.norm(vec.astype(np.float32))) - 1.0) < 0.01
    assert base64.b64decode(g["thumbnail"])[:4] == b"RIFF"
    b = by_id[id_to_str(bad)]
    assert b["state"] == "not_media" and b["sha1"] and "frames" not in b
    o = by_id[id_to_str(other)]
    assert o["state"] == "fingerprinted" and "frames" not in o  # sha1 only until milestone 5


def test_missing_model_file_fails_the_job_not_the_files(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(8, 8))
    params = job_params(spec, model_dir)
    params["path"] = str(model_dir / "absent.onnx")
    with pytest.raises(ModelError):
        fingerprint({"files": [entry(good, lib, "good.png")], "model": params, "max_pixels": 10_000_000}, lambda p: None)


def test_sink_stores_frames_thumbnail_and_queues_match(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(40, 30, gradient=True))
    params = {"files": [entry(good, lib, "good.png")], "model": job_params(spec, model_dir), "max_pixels": 10_000_000, "priority": 50}
    result = fingerprint(params, lambda p: None)
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))
    vconn = reg.vectors_conn("a")
    assert len(fingerprints.frames_of_file(vconn, good, spec.id)) == 1
    [seq] = fingerprints.allocate_keys(vconn, good, spec.id, [0])
    thumb = reg.paths("a").thumbs / id_to_str(good)[:2] / f"{id_to_str(good)}.webp"
    assert thumb.read_bytes()[:4] == b"RIFF"
    [job] = [j for j in jobs_db.list_jobs(conn) if j["type"] == "match"]
    import json

    p = json.loads(job["params"])
    assert p["index_path"] == str(reg.paths("a").index) and p["model"]["id"] == spec.id
    assert p["entries"][0]["seq"] == seq and p["top_k"] == 64 and p["candidate_distance"] == 0.5
    assert job["priority"] == 50 and job["kind"] == "compute"
```

Append to `tests/integration/test_scan_job.py`:

```python
def test_fingerprint_jobs_carry_model_and_limits(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    [job] = jobs_db.list_jobs(conn, state="queued")
    params = json.loads(job["params"])
    assert params["model"]["id"] == "sscd-resnet50-512-v1" and params["model"]["dim"] == 512
    assert params["model"]["path"].endswith("sscd-resnet50-512-v1.onnx")
    assert params["max_pixels"] == 80_000_000 and params["priority"] == 50
```

(`ctx_for` in that file gains `model_dir=reg.data_dir / "models"` in the `IoContext(...)` call; do the same in every other test that builds an `IoContext`.)

Run: `uv run pytest tests/unit/test_fingerprint_vectors.py tests/unit/test_sink_fingerprint.py tests/unit/test_result_sinks.py tests/integration/test_scan_job.py -q` — Expected: FAIL (`SinkContext` missing, `model_dir` kwarg, `job_params`); the vector tests skip locally without libvips and must be run in the test image.

- [ ] **Step 2: Implement**

`visualstore/models/registry.py`: add

```python
def job_params(spec: ModelSpec, model_dir: Path) -> dict:
    """What a compute child needs to know about the model: plain data, no registry access."""
    return {
        "id": spec.id, "path": str(model_dir / spec.file_name()), "dim": spec.dim,
        "input_size": spec.input_size, "mean": list(spec.mean), "std": list(spec.std),
    }
```

`visualstore/jobs/results.py`: replace the module with a `SinkContext` dataclass (fields as in Interfaces), `ResultSink = Callable[[SinkContext], None]`, and `sink_fingerprint(ctx)`:

```python
def sink_fingerprint(ctx: SinkContext) -> None:
    from visualstore.db import files as files_db
    from visualstore.db import fingerprints as fp
    from visualstore.db import groups as groups_db
    from visualstore.db import jobs as jobs_db
    from visualstore.db import pairs as pairs_db
    from visualstore.ids import id_from_str
    from visualstore.jobs.types import PRIORITY_INBOX

    conn, vconn = ctx.conn, ctx.vconn
    dirty = False
    entries_for_match: list[dict] = []
    model = ctx.params.get("model")
    for entry in ctx.result.get("files", []):
        try:
            file_id = id_from_str(entry["id"])
        except ValueError:
            continue
        row = files_db.get_file(conn, file_id)
        if row is None or _stale(row, entry):
            continue
        sha1 = bytes.fromhex(entry["sha1"]) if entry.get("sha1") else None
        files_db.set_fingerprint_result(conn, file_id, sha1=sha1, state=entry["state"])
        if entry.get("frames") and model:
            frames = [
                (f["index"], f.get("time_ms", 0), base64.b64decode(f["vector"]))
                for f in entry["frames"]
            ]
            fp.replace_frames(vconn, file_id, model["id"], frames)
            seqs = fp.allocate_keys(vconn, file_id, model["id"], [f["index"] for f in entry["frames"]])
            entries_for_match.extend(
                {"seq": seq, "vector": f["vector"]} for seq, f in zip(seqs, entry["frames"], strict=True)
            )
        if entry.get("thumbnail"):
            text = entry["id"]
            path = ctx.paths.thumbs / text[:2] / f"{text}.webp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry["thumbnail"]))
        if sha1 is None or entry["state"] != "fingerprinted":
            continue
        bucket = [r for r in files_db.files_by_sha1(conn, sha1) if r["state"] == "fingerprinted"]
        if len(bucket) >= 2:
            hub = bytes(bucket[0]["id"])
            for r in bucket[1:]:
                pairs_db.upsert_sha1_pair(conn, hub, bytes(r["id"]))
            groups_db.mark_dirty(conn, [bytes(r["id"]) for r in bucket])
            dirty = True
    if entries_for_match and model:
        jobs_db.enqueue(
            conn, type="match", kind="compute", priority=int(ctx.params.get("priority", PRIORITY_INBOX)),
            params={
                "model": model, "index_path": str(ctx.paths.index), "entries": entries_for_match,
                "remove": fp.pending_removals(vconn, model["id"]),
                "top_k": int(ctx.settings["similarity.top_k"]),
                "candidate_distance": float(ctx.settings["similarity.candidate_distance"]),
                "priority": int(ctx.params.get("priority", PRIORITY_INBOX)),
            },
        )
    if dirty and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
```

(`_stale` unchanged; `import base64` at the top.) `KIND_OF` gains `"match": "compute"`.

`visualstore/jobs/scheduler.py`: `Scheduler.__init__` gains `model_dir: Path`; `IoContext` gains `model_dir: Path` (passed in `_run_io`); `_finish` builds `SinkContext(conn=conn, vconn=self.registry.vectors_conn(profile), registry=self.registry, profile=profile, paths=self.registry.paths(profile), settings=settings_db.all(conn), model_dir=self.model_dir, params=json.loads(row["params"]), result=result)` and calls `sink(ctx)`. `app.py` passes `model_dir=config.model_dir`.

`visualstore/jobs/compute/handlers.py` — replace `fingerprint`:

```python
_SESSIONS: dict[str, object] = {}


def _session(model: dict):
    from visualstore.models.infer import Session
    from visualstore.models.registry import ModelError, ModelSpec

    path = model["path"]
    if path not in _SESSIONS:
        if not os.path.isfile(path):
            raise ModelError(f"model file missing: {path} (download it or set VISUALSTORE_MODEL_DIR)")
        spec = ModelSpec(
            id=model["id"], url="", sha256="", dim=int(model["dim"]), input_size=int(model["input_size"]),
            mean=tuple(model["mean"]), std=tuple(model["std"]), description="",
        )
        _SESSIONS[path] = Session(path, spec)
    return _SESSIONS[path]


def _embed_image(path: str, model: dict, max_pixels: int) -> tuple[list[dict], str]:
    from visualstore.media.decode import load_image, thumbnail_webp, to_model_input

    decoded = load_image(path, max_pixels)
    session = _session(model)
    vector = session.embed(to_model_input(decoded, int(model["input_size"]), model["mean"], model["std"]))[0]
    frames = [{"index": 0, "time_ms": 0, "vector": base64.b64encode(vector.astype(np.float16).tobytes()).decode("ascii")}]
    thumb = base64.b64encode(thumbnail_webp(decoded, 256)).decode("ascii")
    return frames, thumb


def fingerprint(params: dict, report: Report) -> dict:
    """SHA-1 for every file; vectors and a thumbnail for still images (design §7.2)."""
    from visualstore.media.errors import NotMedia, Unreadable

    entries = params.get("files", [])
    model = params.get("model")
    max_pixels = int(params.get("max_pixels", 80_000_000))
    out = []
    for i, entry in enumerate(entries, start=1):
        path = entry["path"]
        try:
            st = os.stat(path)
            record = {"id": entry["id"], "sha1": sha1_of(path).hex(), "state": "fingerprinted", "size": st.st_size, "mtime": st.st_mtime_ns}
            if model and entry.get("media_type") == "image":
                try:
                    frames, thumb = _embed_image(path, model, max_pixels)
                    record.update({"frames": frames, "thumbnail": thumb, "model_id": model["id"]})
                except NotMedia as exc:
                    record.update({"state": "not_media", "reason": str(exc)[:200]})
                except Unreadable as exc:
                    record.update({"state": "unreadable", "reason": str(exc)[:200]})
            out.append(record)
        except FileNotFoundError:
            out.append({"id": entry["id"], "sha1": None, "state": "missing", "size": 0})
        except OSError:
            out.append({"id": entry["id"], "sha1": None, "state": "unreadable", "size": 0})
        if i % 8 == 0 or i == len(entries):
            report({"done": i, "total": len(entries), "message": os.path.basename(path)})
    return {"files": out}
```

(`import base64` and `import numpy as np` at the top of handlers.py; a `ModelError` from `_session` propagates and fails the job.)

`visualstore/jobs/io/scan.py` `_enqueue_fingerprints(conn, folder, priority, ctx)`: builds `model = job_params(get_spec(ctx.settings["model.id"]), ctx.model_dir)` and adds `"model": model, "max_pixels": int(ctx.settings["media.max_pixels"]), "priority": priority` to each job's params. After deletions and resets: `with closing(ctx.open_vectors()) as vconn: fingerprints.delete_for_files(vconn, to_delete + [fid for fid, _ in plan.changed])`. In `actions.py` `_trash_one` (after `delete_files`): `fingerprints.delete_for_files(vconn, [file_id])` with a `vconn` opened once per `run_apply_action`.

- [ ] **Step 3: Run the tests, then commit**

Run locally: `uv run pytest -q` — Expected: all passed, vector tests skipped. In the test image: `docker build --target test -t visualstore:test . && docker run --rm -v "$PWD/tests:/app/tests" visualstore:test python -m pytest /app/tests/unit/test_fingerprint_vectors.py /app/tests/unit/test_decode.py -q` — Expected: all passed.

```bash
git add visualstore tests
git commit -m "feat: fingerprint computes vectors and thumbnails; sinks get a context; vectors follow deletions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7 (pure part): Compare scope

**Files:**
- Create: `visualstore/domain/scope.py`, `tests/unit/test_domain_scope.py`

**Interfaces:**
- `ScopeInfo(folder_id: bytes, directory: str, compare_scope: str)`; `allowed(a: ScopeInfo, b: ScopeInfo) -> bool` implementing design §7.4 step 4: different Folders → always allowed; same Folder with `all` → allowed; `other_folders` → never; `other_directories` → allowed only when `path_key(directory)` differs.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_domain_scope.py`:

```python
import pytest

from visualstore.domain.scope import ScopeInfo, allowed

F1, F2 = b"\x01" * 16, b"\x02" * 16


@pytest.mark.parametrize(
    "scope,dir_a,dir_b,expected",
    [
        ("all", "x", "x", True),
        ("all", "x", "y", True),
        ("other_directories", "x", "x", False),
        ("other_directories", "x", "X", False),  # NOCASE: same directory
        ("other_directories", "x", "x/sub", True),
        ("other_directories", "x", "y", True),
        ("other_folders", "x", "y", False),
        ("other_folders", "x", "x", False),
    ],
)
def test_same_folder_rules(scope, dir_a, dir_b, expected):
    """Review Focus 2."""
    assert allowed(ScopeInfo(F1, dir_a, scope), ScopeInfo(F1, dir_b, scope)) is expected


@pytest.mark.parametrize("scope", ["all", "other_directories", "other_folders"])
def test_different_folders_are_always_allowed(scope):
    assert allowed(ScopeInfo(F1, "x", scope), ScopeInfo(F2, "x", "other_folders")) is True
```

Run: `uv run pytest tests/unit/test_domain_scope.py -q` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement**

`visualstore/domain/scope.py`:

```python
"""Compare-scope rule (design §7.4 step 4): which file pairs may become vector pairs."""

from __future__ import annotations

from dataclasses import dataclass

from visualstore.domain.paths import path_key


@dataclass(frozen=True)
class ScopeInfo:
    folder_id: bytes
    directory: str
    compare_scope: str  # all | other_directories | other_folders


def allowed(a: ScopeInfo, b: ScopeInfo) -> bool:
    if a.folder_id != b.folder_id:
        return True
    if a.compare_scope == "all":
        return True
    if a.compare_scope == "other_folders":
        return False
    return path_key(a.directory) != path_key(b.directory)
```

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/domain/scope.py tests/unit/test_domain_scope.py
git commit -m "feat: compare-scope rule

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Distance bands

**Files:**
- Create: `visualstore/domain/bands.py`, `tests/unit/test_domain_bands.py`
- Modify: `visualstore/db/groups.py` (`list_groups(..., min_distance_lt=None, min_distance_ge=None)`), `visualstore/api/groups.py` (`band` on every group, `band` filter), `tests/integration/test_api_groups.py`

**Interfaces:**
- `domain.bands.BANDS = ("identical", "near_duplicate", "variant", "candidate")`; `Thresholds(near_duplicate: float, variant: float)`; `band(distance: float | None, has_identical: bool, t: Thresholds) -> str`: `identical` when `has_identical` and (`distance` is None or `distance <= 0`); else `near_duplicate` when `distance <= t.near_duplicate`; `variant` when `<= t.variant`; otherwise `candidate` (`None` without identical → `candidate`). `thresholds_from(settings) -> Thresholds`; `band_bounds(name, t) -> tuple[lo, hi)` for SQL filtering: identical → `[-inf, 0]` with `has_identical`, near_duplicate → `(0, near]`, variant → `(near, variant]`, candidate → `(variant, inf)`.
- API: `GroupOut.band`; `GET /groups?band=near_duplicate` filters; `GET /groups/counts` adds `"bands": {band: count}` over open groups.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_domain_bands.py`:

```python
import pytest

from visualstore.domain.bands import BANDS, Thresholds, band, band_bounds, thresholds_from

T = Thresholds(near_duplicate=0.15, variant=0.3)


@pytest.mark.parametrize(
    "distance,identical,expected",
    [
        (0.0, True, "identical"),
        (None, True, "identical"),
        (0.0, False, "near_duplicate"),  # distance 0 without a sha1 edge: still a near duplicate
        (0.15, False, "near_duplicate"),
        (0.1500001, False, "variant"),
        (0.3, False, "variant"),
        (0.31, False, "candidate"),
        (None, False, "candidate"),
    ],
)
def test_band_boundaries_are_inclusive_at_the_top(distance, identical, expected):
    assert band(distance, identical, T) == expected


def test_thresholds_from_settings_and_bounds():
    t = thresholds_from({"similarity.near_duplicate": 0.2, "similarity.variant": 0.4})
    assert t == Thresholds(0.2, 0.4)
    assert band_bounds("near_duplicate", t) == (0.0, 0.2)
    assert band_bounds("variant", t) == (0.2, 0.4)
    lo, hi = band_bounds("candidate", t)
    assert lo == 0.4 and hi == float("inf")
    assert BANDS[0] == "identical"
```

Append to `tests/integration/test_api_groups.py`:

```python
def test_groups_carry_bands_and_filter_by_band(client, seeded):
    s = seeded
    listed = client.get("/api/p/a/groups").json()
    assert listed[0]["band"] == "identical"
    assert client.get("/api/p/a/groups?band=identical").json()[0]["id"] == id_to_str(s["gid"])
    assert client.get("/api/p/a/groups?band=variant").json() == []
    assert client.get("/api/p/a/groups?band=nope").status_code == 400
    counts = client.get("/api/p/a/groups/counts").json()
    assert counts["bands"] == {"identical": 1, "near_duplicate": 0, "variant": 0, "candidate": 0}
```

Run: `uv run pytest tests/unit/test_domain_bands.py tests/integration/test_api_groups.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/domain/bands.py`:

```python
"""Threshold bands over cosine distance (design §7.6)."""

from __future__ import annotations

from dataclasses import dataclass

BANDS = ("identical", "near_duplicate", "variant", "candidate")


@dataclass(frozen=True)
class Thresholds:
    near_duplicate: float
    variant: float


def thresholds_from(settings: dict) -> Thresholds:
    return Thresholds(
        near_duplicate=float(settings["similarity.near_duplicate"]),
        variant=float(settings["similarity.variant"]),
    )


def band(distance: float | None, has_identical: bool, t: Thresholds) -> str:
    if has_identical and (distance is None or distance <= 0):
        return "identical"
    if distance is None:
        return "candidate"
    if distance <= t.near_duplicate:
        return "near_duplicate"
    if distance <= t.variant:
        return "variant"
    return "candidate"


def band_bounds(name: str, t: Thresholds) -> tuple[float, float]:
    """(lo, hi] on min_distance for the non-identical bands; identical is has_identical = 1."""
    if name == "identical":
        return (float("-inf"), 0.0)
    if name == "near_duplicate":
        return (0.0, t.near_duplicate)
    if name == "variant":
        return (t.near_duplicate, t.variant)
    if name == "candidate":
        return (t.variant, float("inf"))
    raise ValueError(f"band must be one of {BANDS}")
```

`visualstore/db/groups.py` `list_groups` gains `min_distance_gt: float | None = None, min_distance_le: float | None = None` → `g.min_distance > ?` / `g.min_distance <= ?` clauses, and `count_by_band(conn, t: Thresholds) -> dict[str, int]` over `status = 'open'` groups: identical = `has_identical = 1`; others by bounds with `has_identical = 0`.

`visualstore/api/groups.py`: `_group_out` adds `"band": band(row["min_distance"], bool(row["has_identical"]), thresholds_from(settings_db.all(conn)))`; `list_groups` accepts `band: str | None` (400 unless in `BANDS`): identical → `has_identical=True`; others → `has_identical=False` plus `band_bounds` → `min_distance_gt` / `min_distance_le` (skip infinite bounds); `group_counts` adds `"bands": groups_db.count_by_band(conn, t)`.

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/domain/bands.py visualstore/db/groups.py visualstore/api/groups.py tests/unit/test_domain_bands.py tests/integration/test_api_groups.py
git commit -m "feat: distance bands on groups with band filter and counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: usearch index wrapper

**Files:**
- Create: `visualstore/index/__init__.py`, `visualstore/index/usearch_index.py`, `tests/unit/test_vector_index.py`

**Interfaces:**
- `index.usearch_index.IndexError_(Exception)`; `VectorIndex` with `open_or_create(path: Path, dim: int) -> VectorIndex` (restores an existing file; raises `IndexError_` when the file is corrupt or its `dimensions` differ), `empty(dim) -> VectorIndex` (in memory), `.add(keys: Sequence[int], vectors: np.ndarray)` (float32 `(n, dim)`), `.remove(keys) -> int` (keys not present are ignored), `.search(vector: np.ndarray, k: int) -> list[tuple[int, float]]` sorted by distance, `.save(path)` atomically (write `<path>.tmp`, replace), `.count`, `.dim`; `inspect(path) -> dict | None` (`{"dimensions", "count"}` from `Index.metadata`, `None` when missing, `IndexError_` when unreadable).
- Metric cosine, scalar `f16`, keys `uint64`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_vector_index.py`:

```python
import numpy as np
import pytest

from visualstore.index.usearch_index import IndexError_, VectorIndex, inspect


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_add_search_remove_round_trip(tmp_path):
    idx = VectorIndex.empty(4)
    idx.add([10, 11, 12], np.stack([unit([1, 0, 0, 0]), unit([0.9, 0.1, 0, 0]), unit([0, 0, 1, 0])]))
    hits = idx.search(unit([1, 0, 0, 0]), k=3)
    assert [k for k, _ in hits] == [10, 11, 12]
    assert hits[0][1] < 1e-3 and 0 < hits[1][1] < 0.02 and abs(hits[2][1] - 1.0) < 1e-3
    assert idx.remove([11, 999]) == 1 and idx.count == 2
    path = tmp_path / "index.usearch"
    idx.save(path)
    assert path.is_file() and not (tmp_path / "index.usearch.tmp").exists()
    again = VectorIndex.open_or_create(path, 4)
    assert again.count == 2 and [k for k, _ in again.search(unit([0, 0, 1, 0]), 1)] == [12]
    assert inspect(path) == {"dimensions": 4, "count": 2}


def test_missing_file_creates_empty_and_dimension_mismatch_is_an_error(tmp_path):
    path = tmp_path / "new.usearch"
    assert inspect(path) is None
    idx = VectorIndex.open_or_create(path, 8)
    assert idx.count == 0 and idx.dim == 8
    idx.add([1], unit(np.arange(1, 9))[None, :])
    idx.save(path)
    with pytest.raises(IndexError_, match="dimension"):
        VectorIndex.open_or_create(path, 16)


def test_corrupt_file_is_an_index_error(tmp_path):
    path = tmp_path / "bad.usearch"
    path.write_bytes(b"not an index at all" * 10)
    with pytest.raises(IndexError_):
        VectorIndex.open_or_create(path, 4)
    with pytest.raises(IndexError_):
        inspect(path)


def test_search_on_empty_index_returns_nothing():
    assert VectorIndex.empty(4).search(unit([1, 0, 0, 0]), 5) == []
```

Run: `uv run pytest tests/unit/test_vector_index.py -q` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement**

`visualstore/index/__init__.py`: empty. `visualstore/index/usearch_index.py`:

```python
"""Thin wrapper around usearch for one profile's frame index (design §7.4, ADR-0005)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from usearch.index import Index


class IndexError_(Exception):
    pass


def inspect(path: Path) -> dict | None:
    if not Path(path).is_file():
        return None
    try:
        meta = Index.metadata(str(path))
    except Exception as exc:  # noqa: BLE001 - usearch raises plain Exception/RuntimeError
        raise IndexError_(f"{path} is not a readable usearch index: {exc}") from exc
    if not meta:
        raise IndexError_(f"{path} is not a readable usearch index")
    return {"dimensions": int(meta["dimensions"]), "count": int(meta["count_present"])}


class VectorIndex:
    def __init__(self, index: Index, dim: int) -> None:
        self._index = index
        self.dim = dim

    @classmethod
    def empty(cls, dim: int) -> VectorIndex:
        return cls(Index(ndim=dim, metric="cos", dtype="f16"), dim)

    @classmethod
    def open_or_create(cls, path: Path, dim: int) -> VectorIndex:
        meta = inspect(path)
        if meta is None:
            return cls.empty(dim)
        if meta["dimensions"] != dim:
            raise IndexError_(
                f"{path} has dimension {meta['dimensions']}, the active model needs {dim}"
            )
        try:
            index = Index.restore(str(path), view=False)
        except Exception as exc:  # noqa: BLE001
            raise IndexError_(f"could not load {path}: {exc}") from exc
        if index is None:
            raise IndexError_(f"could not load {path}")
        return cls(index, dim)

    @property
    def count(self) -> int:
        return len(self._index)

    def add(self, keys: Sequence[int], vectors: np.ndarray) -> None:
        if len(keys) == 0:
            return
        arr = np.ascontiguousarray(vectors, dtype=np.float32).reshape(len(keys), self.dim)
        self._index.add(np.asarray(keys, dtype=np.uint64), arr, copy=True)

    def remove(self, keys: Sequence[int]) -> int:
        present = [k for k in keys if k in self._index]
        if not present:
            return 0
        self._index.remove(np.asarray(present, dtype=np.uint64))
        return len(present)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.count == 0 or k <= 0:
            return []
        matches = self._index.search(np.ascontiguousarray(vector, dtype=np.float32), min(k, self.count))
        pairs = [(int(key), float(dist)) for key, dist in zip(matches.keys, matches.distances, strict=True)]
        pairs.sort(key=lambda p: p[1])
        return pairs

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        self._index.save(str(tmp))
        tmp.replace(path)
```

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/index tests/unit/test_vector_index.py
git commit -m "feat: usearch index wrapper with atomic save and metadata inspection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7 (compute part): `match` job and its sink

**Files:**
- Modify: `visualstore/jobs/compute/handlers.py`, `visualstore/jobs/results.py`, `visualstore/db/pairs.py`, `visualstore/jobs/types.py`
- Create: `tests/unit/test_match.py`

**Interfaces:**
- Handler `match(params, report)`: params `{"model": {...}, "index_path", "entries": [{"seq", "vector"}], "remove": [seq], "top_k", "candidate_distance"}`. Opens the index (`open_or_create`), removes `remove`, adds entries, saves, then searches every new vector with `top_k`; returns `{"hits": [[query_seq, hit_seq, distance], ...] within candidate_distance and hit != query, "added", "removed", "count"}`. An `IndexError_` propagates (the job fails and shows the reason).
- `pairs_db.upsert_vector_pair(conn, a, b, distance, model_id) -> None`: insert `source='vector'`; on conflict update `distance` and `model_id` only when the existing `source != 'sha1'` (sha1 pairs stay identical); judgments untouched.
- `results.sink_match(ctx)`: resolves hit and query keys (`fp.resolve_keys`), aggregates to unordered file pairs with the minimum distance (ignoring same-file hits), loads `ScopeInfo` for every involved file from `file` + `folder`, keeps pairs that pass `domain.scope.allowed`, upserts them, marks both files dirty, enqueues `regroup` if none active, drops the keys listed in `params["remove"]`, and sets `index_meta` `model_id`/`dim` when absent. Registered as `RESULT_SINKS["match"]`; `KIND_OF["match"] = "compute"`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_match.py`:

```python
import base64

import numpy as np
import pytest

from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, fingerprints, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import IndexError_, VectorIndex, inspect
from visualstore.jobs.compute.handlers import match
from visualstore.jobs.results import RESULT_SINKS, sink_match
from visualstore.profiles import ProfileRegistry

M = {"id": "tiny-test-4-v1", "dim": 4, "path": "", "input_size": 16, "mean": [0, 0, 0], "std": [1, 1, 1]}


def b64(v):
    v = np.asarray(v, dtype=np.float32)
    return base64.b64encode((v / np.linalg.norm(v)).astype(np.float16).tobytes()).decode()


def test_match_handler_adds_searches_and_filters_by_distance(tmp_path):
    index_path = tmp_path / "index.usearch"
    params = {
        "model": M, "index_path": str(index_path), "remove": [],
        "entries": [{"seq": 1, "vector": b64([1, 0, 0, 0])}, {"seq": 2, "vector": b64([0.95, 0.05, 0, 0])}, {"seq": 3, "vector": b64([0, 0, 1, 0])}],
        "top_k": 10, "candidate_distance": 0.5,
    }
    result = match(params, lambda p: None)
    assert result["added"] == 3 and result["removed"] == 0 and result["count"] == 3
    hits = {(q, h) for q, h, _ in result["hits"]}
    assert (1, 2) in hits and (2, 1) in hits and (1, 3) not in hits  # 3 is at distance 1.0
    assert all(q != h for q, h, _ in result["hits"])
    assert inspect(index_path)["count"] == 3

    again = match({**params, "entries": [{"seq": 4, "vector": b64([0, 0, 0.98, 0.02])}], "remove": [2]}, lambda p: None)
    assert again["removed"] == 1 and again["count"] == 3
    assert {(q, h) for q, h, _ in again["hits"]} == {(4, 3)}


def test_match_handler_refuses_a_foreign_index(tmp_path):
    index_path = tmp_path / "index.usearch"
    other = VectorIndex.empty(8)
    other.add([1], np.ones((1, 8), dtype=np.float32) / np.sqrt(8))
    other.save(index_path)
    with pytest.raises(IndexError_):
        match({"model": M, "index_path": str(index_path), "entries": [], "remove": [], "top_k": 5, "candidate_distance": 0.5}, lambda p: None)


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    for d in ("arch", "inbox"):
        (tmp_path / d).mkdir()
    arch = folders.create_folder(conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="other_directories")
    inbox = folders.create_folder(conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, directory, name):
        fid = files.insert_file(conn, folder_id=folder, directory=directory, name=name, extension_id=ext, size=1, mtime=1)
        files.set_fingerprint_result(conn, fid, sha1=bytes([len(name)]) * 20, state="fingerprinted")
        fingerprints.replace_frames(vconn, fid, M["id"], [(0, 0, b"\x00" * 8)])
        [seq] = fingerprints.allocate_keys(vconn, fid, M["id"], [0])
        return fid, seq

    yield reg, conn, vconn, arch, inbox, add
    reg.close()


def test_sink_match_applies_scope_and_writes_pairs(world):
    """Review Focus 2."""
    reg, conn, vconn, arch, inbox, add = world
    a1, s1 = add(arch, "set", "a1.jpg")
    a2, s2 = add(arch, "set", "a2.jpg")       # same Folder + directory as a1: filtered
    a3, s3 = add(arch, "other", "a3.jpg")     # same Folder, other directory: allowed
    i1, s4 = add(inbox, "set", "i1.jpg")      # different Folder: allowed
    result = {"hits": [[s1, s2, 0.05], [s1, s3, 0.1], [s1, s4, 0.2], [s4, s1, 0.19], [s1, 999, 0.01], [s1, s1, 0.0]], "added": 4, "removed": 0, "count": 4}
    params = {"model": M, "entries": [{"seq": s, "vector": ""} for s in (s1, s2, s3, s4)], "remove": [], "top_k": 10, "candidate_distance": 0.5}
    sink_match(make_sink_ctx(reg, "a", params, result))
    got = {tuple(sorted((bytes(p["file_a"]), bytes(p["file_b"])))): p for p in pairs.edges_touching(conn, [a1])}
    assert set(got) == {tuple(sorted((a1, a3))), tuple(sorted((a1, i1)))}
    assert got[tuple(sorted((a1, i1)))]["distance"] == pytest.approx(0.19)  # min over both directions
    assert all(p["source"] == "vector" and p["model_id"] == M["id"] for p in got.values())
    assert set(groups.pop_dirty(conn)) == {a1, a3, i1}
    assert jobs_db.has_active(conn, "regroup")
    assert fingerprints.get_meta(vconn, "model_id") == M["id"] and fingerprints.get_meta(vconn, "dim") == "4"
    assert RESULT_SINKS["match"] is sink_match


def test_sink_match_keeps_sha1_pairs_and_judgments_and_drops_removed_keys(world):
    reg, conn, vconn, arch, inbox, add = world
    a, sa = add(arch, "x", "a.jpg")
    b, sb = add(inbox, "x", "b.jpg")
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.set_judgment(conn, a, b, "unrelated", "user")
    fingerprints.delete_for_files(vconn, [b"\x09" * 16])
    gone = fingerprints.allocate_keys(vconn, b"\x09" * 16, M["id"], [0])  # a key for a deleted file
    fingerprints.delete_for_files(vconn, [b"\x09" * 16])
    params = {"model": M, "entries": [], "remove": gone, "top_k": 10, "candidate_distance": 0.5}
    sink_match(make_sink_ctx(reg, "a", params, {"hits": [[sa, sb, 0.3]], "added": 0, "removed": 1, "count": 2}))
    [p] = pairs.pairs_of_file(conn, a)
    assert p["source"] == "sha1" and p["distance"] == 0.0 and p["judgment"] == "unrelated"
    assert fingerprints.pending_removals(vconn, M["id"]) == []
```

Run: `uv run pytest tests/unit/test_match.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/db/pairs.py`:

```python
def upsert_vector_pair(conn: sqlite3.Connection, a: bytes, b: bytes, distance: float, model_id: str) -> None:
    fa, fb = ordered(a, b)
    with conn:
        conn.execute(
            "INSERT INTO pair (file_a, file_b, distance, source, model_id)"
            " VALUES (?, ?, ?, 'vector', ?)"
            " ON CONFLICT(file_a, file_b) DO UPDATE SET"
            " distance = CASE WHEN pair.source = 'sha1' THEN pair.distance ELSE excluded.distance END,"
            " model_id = CASE WHEN pair.source = 'sha1' THEN pair.model_id ELSE excluded.model_id END",
            (fa, fb, distance, model_id),
        )
```

`visualstore/jobs/compute/handlers.py`:

```python
def match(params: dict, report: Report) -> dict:
    """Add a batch of vectors to the profile index and return raw neighbour hits (design §7.4).
    The sink applies compare scope and writes pairs; this function never sees the database."""
    from visualstore.index.usearch_index import VectorIndex

    model = params["model"]
    dim = int(model["dim"])
    index = VectorIndex.open_or_create(Path(params["index_path"]), dim)
    removed = index.remove([int(s) for s in params.get("remove", [])])
    entries = params.get("entries", [])
    keys = [int(e["seq"]) for e in entries]
    vectors = np.array(
        [np.frombuffer(base64.b64decode(e["vector"]), dtype=np.float16).astype(np.float32) for e in entries],
        dtype=np.float32,
    ).reshape(len(entries), dim)
    index.add(keys, vectors)
    index.save(Path(params["index_path"]))
    limit = float(params.get("candidate_distance", 0.5))
    top_k = int(params.get("top_k", 64))
    hits: list[list] = []
    for i, key in enumerate(keys):
        for hit_key, distance in index.search(vectors[i], top_k):
            if hit_key != key and distance <= limit:
                hits.append([key, hit_key, round(distance, 6)])
        if (i + 1) % 16 == 0 or i + 1 == len(keys):
            report({"done": i + 1, "total": len(keys), "message": f"{len(hits)} hits"})
    return {"hits": hits, "added": len(keys), "removed": removed, "count": index.count}
```

(register `"match": match` in `HANDLERS`; `from pathlib import Path` at the top.)

`visualstore/jobs/results.py`:

```python
def sink_match(ctx: SinkContext) -> None:
    from visualstore.db import fingerprints as fp
    from visualstore.db import groups as groups_db
    from visualstore.db import jobs as jobs_db
    from visualstore.db import pairs as pairs_db
    from visualstore.domain.scope import ScopeInfo, allowed
    from visualstore.jobs.types import PRIORITY_INBOX

    conn, vconn = ctx.conn, ctx.vconn
    model = ctx.params["model"]
    hits = ctx.result.get("hits", [])
    seqs = {int(h[0]) for h in hits} | {int(h[1]) for h in hits}
    owner = fp.resolve_keys(vconn, seqs)
    best: dict[tuple[bytes, bytes], float] = {}
    for q, h, distance in hits:
        a = owner.get(int(q))
        b = owner.get(int(h))
        if a is None or b is None or a[0] == b[0]:
            continue
        key = tuple(sorted((a[0], b[0])))
        if key not in best or distance < best[key]:
            best[key] = float(distance)
    involved = {f for pair in best for f in pair}
    scopes: dict[bytes, ScopeInfo] = {}
    if involved:
        marks = ",".join("?" * len(involved))
        for r in conn.execute(
            f"SELECT f.id, f.folder_id, f.directory, fo.compare_scope FROM file f"
            f" JOIN folder fo ON fo.id = f.folder_id WHERE f.id IN ({marks})",
            list(involved),
        ):
            scopes[bytes(r["id"])] = ScopeInfo(bytes(r["folder_id"]), r["directory"], r["compare_scope"])
    dirty: set[bytes] = set()
    for (a, b), distance in best.items():
        if a not in scopes or b not in scopes or not allowed(scopes[a], scopes[b]):
            continue
        pairs_db.upsert_vector_pair(conn, a, b, distance, model["id"])
        dirty.update((a, b))
    if dirty:
        groups_db.mark_dirty(conn, dirty)
        if not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    removed = [int(s) for s in ctx.params.get("remove", [])]
    if removed:
        fp.drop_keys(vconn, removed)
    if fp.get_meta(vconn, "model_id") is None:
        fp.set_meta(vconn, "model_id", model["id"])
        fp.set_meta(vconn, "dim", str(int(model["dim"])))


RESULT_SINKS["match"] = sink_match
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_match.py tests/unit/test_db_pairs_groups.py -q` — Expected: all passed.

```bash
git add visualstore/jobs/compute/handlers.py visualstore/jobs/results.py visualstore/db/pairs.py visualstore/jobs/types.py tests/unit/test_match.py
git commit -m "feat: match job with scope-filtered vector pairs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Index rebuild, per-profile exclusivity, start-up consistency check

**Files:**
- Create: `visualstore/jobs/io/rebuild.py`, `visualstore/jobs/startup.py`, `tests/unit/test_rebuild_and_startup.py`
- Modify: `visualstore/jobs/compute/handlers.py` (`build_index`), `visualstore/jobs/results.py` (`sink_build_index`, guard in `sink_fingerprint`), `visualstore/jobs/scheduler.py`, `visualstore/db/jobs.py` (`next_queued(..., exclude_types)`), `visualstore/jobs/types.py`, `visualstore/jobs/io/__init__.py`, `visualstore/app.py`, `tests/unit/test_scheduler.py`

**Interfaces:**
- Io job `rebuild_index` (params `{}`): exports every live `(seq, vector)` of the active model to `paths.root / "index.export.npz"` (`keys` uint64, `vectors` float16 `(n, dim)`), then enqueues compute `build_index` `{"index_path", "export_path", "dim", "model_id"}` at the same priority. With zero vectors it writes an empty index itself and sets meta.
- Compute `build_index`: builds a fresh `VectorIndex` from the export, saves it over `index_path` (atomic), deletes the export, returns `{"count", "model_id", "dim"}`. `sink_build_index` sets `index_meta` `model_id`, `dim`, `built_at`.
- Scheduler: `EXCLUSIVE_COMPUTE = {"match", "build_index"}`; at most one in flight per profile; `jobs_db.next_queued(conn, kind, exclude_types=())` lets the scheduler pick the next non-exclusive job for a busy profile. `in_flight_types: dict[str, str]` tracks types.
- `sink_fingerprint` guard: before enqueuing `match`, if the index file is missing or `index_meta.model_id != model.id`, enqueue `rebuild_index` (if none active) instead of `match` — the rebuild includes the vectors just written. (Review Focus 1.)
- `startup.check_indexes(registry) -> int`: for each profile with vectors for the active model, enqueue `rebuild_index` when the index file is missing, unreadable, has another dimension, or `index_meta.model_id` differs; called from the lifespan after `requeue_interrupted`. Returns the number enqueued.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_rebuild_and_startup.py`:

```python
import json

import numpy as np
import pytest

from tests.unit.sinkctx import make_sink_ctx
from tests.unit.test_scheduler import FakePool
from visualstore.db import fingerprints, folders, files
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import VectorIndex, inspect
from visualstore.jobs.compute.handlers import build_index
from visualstore.jobs.io.rebuild import run_rebuild_index
from visualstore.jobs.results import sink_build_index, sink_fingerprint
from visualstore.jobs.scheduler import IoContext, Scheduler
from visualstore.jobs.startup import check_indexes
from visualstore.profiles import ProfileRegistry

MODEL = "sscd-resnet50-512-v1"


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    yield reg
    reg.close()


def ctx_for(reg, params=None):
    conn = reg.conn("a")
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path,
                     settings=settings_db.all(conn), params=params or {}, report=lambda p: None, model_dir=reg.data_dir / "models")


def seed_vectors(reg, n=3, dim=512):
    vconn = reg.vectors_conn("a")
    rng = np.random.default_rng(0)
    ids = []
    for i in range(n):
        fid = bytes([i + 1]) * 16
        vec = rng.normal(size=dim).astype(np.float32)
        vec /= np.linalg.norm(vec)
        fingerprints.replace_frames(vconn, fid, MODEL, [(0, 0, vec.astype(np.float16).tobytes())])
        fingerprints.allocate_keys(vconn, fid, MODEL, [0])
        ids.append(fid)
    return ids


def test_rebuild_exports_then_build_writes_index_and_meta(world):
    reg = world
    seed_vectors(reg, n=3)
    result = run_rebuild_index(ctx_for(reg))
    assert result["exported"] == 3
    conn = reg.conn("a")
    [job] = jobs_db.list_jobs(conn, state="queued")
    params = json.loads(job["params"])
    assert job["type"] == "build_index" and params["dim"] == 512 and params["model_id"] == MODEL
    built = build_index(params, lambda p: None)
    assert built["count"] == 3 and inspect(reg.paths("a").index) == {"dimensions": 512, "count": 3}
    assert not (reg.paths("a").root / "index.export.npz").exists()
    sink_build_index(make_sink_ctx(reg, "a", params, built))
    vconn = reg.vectors_conn("a")
    assert fingerprints.get_meta(vconn, "model_id") == MODEL and fingerprints.get_meta(vconn, "dim") == "512"
    assert fingerprints.get_meta(vconn, "built_at")


def test_rebuild_with_no_vectors_writes_an_empty_index(world):
    reg = world
    result = run_rebuild_index(ctx_for(reg))
    assert result == {"exported": 0, "built": True}
    assert inspect(reg.paths("a").index) == {"dimensions": 512, "count": 0}
    assert jobs_db.list_jobs(reg.conn("a"), state="queued") == []


def test_startup_check_enqueues_rebuild_when_index_is_stale(world):
    """Review Focus 1."""
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    assert check_indexes(reg) == 0  # nothing to index yet
    seed_vectors(reg, n=2)
    assert check_indexes(reg) == 1  # vectors but no index file
    assert check_indexes(reg) == 0  # already queued
    for j in jobs_db.list_jobs(conn):
        jobs_db.cancel(conn, j["id"])
    idx = VectorIndex.empty(512)
    idx.save(reg.paths("a").index)
    fingerprints.set_meta(vconn, "model_id", "some-other-model")
    assert check_indexes(reg) == 1  # meta names another model
    for j in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, j["id"])
    fingerprints.set_meta(vconn, "model_id", MODEL)
    assert check_indexes(reg) == 0
    reg.paths("a").index.write_bytes(b"corrupt")
    assert check_indexes(reg) == 1


def test_fingerprint_sink_routes_to_rebuild_when_index_is_missing(world):
    reg = world
    conn = reg.conn("a")
    (reg.data_dir / "lib").mkdir()
    folder = folders.create_folder(conn, name="L", path=str(reg.data_dir / "lib"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(conn, folder_id=folder, directory="", name="a.jpg", extension_id=ext, size=1, mtime=0)
    import base64

    vec = base64.b64encode((np.ones(512, dtype=np.float32) / np.sqrt(512)).astype(np.float16).tobytes()).decode()
    model = {"id": MODEL, "dim": 512, "path": "", "input_size": 288, "mean": [0, 0, 0], "std": [1, 1, 1]}
    result = {"files": [{"id": id_to_str(fid), "sha1": "aa" * 20, "state": "fingerprinted", "size": 1, "frames": [{"index": 0, "time_ms": 0, "vector": vec}], "model_id": MODEL}]}
    sink_fingerprint(make_sink_ctx(reg, "a", {"model": model, "priority": 50}, result))
    types = sorted(j["type"] for j in jobs_db.list_jobs(conn, state="queued"))
    assert types == ["rebuild_index"]  # not "match": there is no index yet


async def test_scheduler_runs_one_exclusive_compute_job_per_profile(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    reg.create("b")
    pool = FakePool(size=4)
    sched = Scheduler(reg, pool, io_handlers={}, backup_dir=tmp_path / "bk", model_dir=tmp_path / "models", plan_interval=1000)
    a, b = reg.conn("a"), reg.conn("b")
    jobs_db.enqueue(a, type="match", kind="compute", priority=100, params={})
    jobs_db.enqueue(a, type="match", kind="compute", priority=90, params={})
    jobs_db.enqueue(a, type="ping", kind="compute", priority=80, params={})
    jobs_db.enqueue(b, type="build_index", kind="compute", priority=70, params={})
    await sched.tick()
    submitted = [(r.profile, r.type) for r in pool.submitted]
    assert submitted.count(("a", "match")) == 1 and ("a", "ping") in submitted and ("b", "build_index") in submitted
    assert len(submitted) == 3  # the second match for a waits
    reg.close()
```

Update `tests/unit/test_scheduler.py` fixtures to pass `model_dir=tmp_path / "models"` to `Scheduler(...)`.

Run: `uv run pytest tests/unit/test_rebuild_and_startup.py tests/unit/test_scheduler.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/db/jobs.py` `next_queued(conn, kind, exclude_types: tuple[str, ...] = ())`: when `exclude_types` is non-empty add `AND type NOT IN (...)`.

`visualstore/jobs/scheduler.py`:

```python
EXCLUSIVE_COMPUTE = {"match", "build_index"}  # one index writer per profile (design §6.4)
```

`in_flight_types: dict[str, str]`; `_dispatch_compute` becomes:

```python
    def _dispatch_compute(self) -> None:
        while len(self.in_flight) < self.pool.size:
            picked = self._pick_compute()
            if picked is None:
                return
            profile, row = picked
            ...  # unchanged body; also self.in_flight_types[job_id] = row["type"]

    def _busy_exclusive(self) -> set[str]:
        return {self.in_flight[j] for j, t in self.in_flight_types.items() if t in EXCLUSIVE_COMPUTE}

    def _pick_compute(self):
        busy = self._busy_exclusive()
        best = None
        for name in self.registry.names():
            exclude = tuple(EXCLUSIVE_COMPUTE) if name in busy else ()
            row = jobs_db.next_queued(self.registry.conn(name), "compute", exclude_types=exclude)
            if row is None:
                continue
            key = (-int(row["priority"]), str(row["created_at"]))
            if best is None or key < best[0]:
                best = (key, name, row)
        return None if best is None else (best[1], best[2])
```

`_forget` also pops `in_flight_types`. `Scheduler.__init__` takes `model_dir`.

`visualstore/jobs/io/rebuild.py`:

```python
"""Index rebuild (design §7.4 last paragraph, §7.7): export live vectors, let a child build."""

from __future__ import annotations

from contextlib import closing

import numpy as np

from visualstore.clock import now_iso
from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX
from visualstore.models.registry import get_spec

EXPORT_NAME = "index.export.npz"


def run_rebuild_index(ctx: IoContext) -> dict:
    spec = get_spec(ctx.settings["model.id"])
    export = ctx.paths.root / EXPORT_NAME
    with closing(ctx.open_vectors()) as vconn, closing(ctx.open_metadata()) as conn:
        keys: list[int] = []
        chunks: list[np.ndarray] = []
        for rows in fp.iter_vectors(vconn, spec.id):
            keys.extend(r["seq"] for r in rows)
            chunks.append(np.array([np.frombuffer(r["vector"], dtype=np.float16) for r in rows]))
            ctx.report({"done": len(keys), "total": 0, "message": f"exported {len(keys)} vectors"})
        if not keys:
            VectorIndex.empty(spec.dim).save(ctx.paths.index)
            fp.set_meta(vconn, "model_id", spec.id)
            fp.set_meta(vconn, "dim", str(spec.dim))
            fp.set_meta(vconn, "built_at", now_iso())
            return {"exported": 0, "built": True}
        vectors = np.concatenate(chunks).reshape(len(keys), spec.dim)
        np.savez(export, keys=np.asarray(keys, dtype=np.uint64), vectors=vectors)
        priority = int(ctx.params.get("priority", PRIORITY_INBOX))
        jobs_db.enqueue(
            conn, type="build_index", kind="compute", priority=priority,
            params={"index_path": str(ctx.paths.index), "export_path": str(export), "dim": spec.dim, "model_id": spec.id},
        )
        return {"exported": len(keys), "built": False}
```

`handlers.py`:

```python
def build_index(params: dict, report: Report) -> dict:
    from visualstore.index.usearch_index import VectorIndex

    data = np.load(params["export_path"])
    keys = data["keys"].astype(np.uint64)
    vectors = data["vectors"].astype(np.float32)
    index = VectorIndex.empty(int(params["dim"]))
    step = 4096
    for start in range(0, len(keys), step):
        index.add(keys[start : start + step].tolist(), vectors[start : start + step])
        report({"done": min(start + step, len(keys)), "total": len(keys), "message": "building index"})
    index.save(Path(params["index_path"]))
    os.unlink(params["export_path"])
    return {"count": index.count, "model_id": params["model_id"], "dim": int(params["dim"])}
```

`results.py`:

```python
def sink_build_index(ctx: SinkContext) -> None:
    from visualstore.clock import now_iso
    from visualstore.db import fingerprints as fp

    fp.set_meta(ctx.vconn, "model_id", ctx.result["model_id"])
    fp.set_meta(ctx.vconn, "dim", str(ctx.result["dim"]))
    fp.set_meta(ctx.vconn, "built_at", now_iso())


RESULT_SINKS["build_index"] = sink_build_index
```

and in `sink_fingerprint`, replace the plain `match` enqueue with:

```python
    if entries_for_match and model:
        index_ok = ctx.paths.index.is_file() and fp.get_meta(vconn, "model_id") == model["id"]
        if index_ok:
            jobs_db.enqueue(conn, type="match", ...)  # as before
        elif not (jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index")):
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=int(ctx.params.get("priority", PRIORITY_INBOX)))
```

`visualstore/jobs/startup.py`:

```python
"""Start-up consistency checks (design §7.4: rebuild when the index is missing or stale)."""

from __future__ import annotations

from visualstore.db import fingerprints as fp
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.index.usearch_index import IndexError_, inspect
from visualstore.jobs.types import PRIORITY_INBOX
from visualstore.models.registry import ModelError, get_spec
from visualstore.profiles import ProfileRegistry


def index_is_stale(registry: ProfileRegistry, name: str) -> bool:
    conn, vconn = registry.conn(name), registry.vectors_conn(name)
    try:
        spec = get_spec(settings_db.get(conn, "model.id"))
    except ModelError:
        return False
    if fp.vector_count(vconn, spec.id) == 0:
        return False
    if fp.get_meta(vconn, "model_id") != spec.id:
        return True
    try:
        meta = inspect(registry.paths(name).index)
    except IndexError_:
        return True
    return meta is None or meta["dimensions"] != spec.dim


def check_indexes(registry: ProfileRegistry) -> int:
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index"):
            continue
        if index_is_stale(registry, name):
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
            enqueued += 1
    return enqueued
```

`KIND_OF` gains `"rebuild_index": "io"`, `"build_index": "compute"`; `IO_HANDLERS["rebuild_index"] = run_rebuild_index`; `HANDLERS["build_index"] = build_index`; `app.py` calls `check_indexes(registry)` after `scheduler.requeue_interrupted()`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit -q` — Expected: all passed.

```bash
git add visualstore tests
git commit -m "feat: index rebuild pipeline, exclusive compute per profile, start-up index check

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `eval` io job and report

**Files:**
- Create: `visualstore/jobs/io/eval.py`, `tests/integration/test_eval_job.py`
- Modify: `visualstore/jobs/io/__init__.py`, `visualstore/jobs/types.py`

**Interfaces:**
- Io job `eval`, params `{"db_path": "<v1 .db>", "negatives": 5000, "seed": 1}`. `run_eval(ctx) -> dict` summary; raises `EvalError` when the v1 db is unreadable or fewer than 20 labelled positive pairs have vectors for the active model. Reads only; never writes `pair`, groups or judgments.
- Labels (design §7.6): positives = v1 `SimilarFile` rows with `IgnoredMode = 1` plus byte-identical pairs from our `file.sha1` buckets (capped at 20 000, seeded sample); labelled negatives = `IgnoredMode = 2`; random negatives = `negatives` seeded pairs of files from different Folders. v1 ids map through `uuid.UUID(bytes_le=...)` (reuse `import_v1.gid`, `_open_v1`); rows pointing at files we do not have are counted as `orphans`.
- Distances: frame 0 vectors of the active model, cosine distance `1 - dot`. Metrics per set: `count`, `mean`, `median`, `p05`, `p95`, 20-bin histogram over `[0, 1]`; thresholds at positive recall 0.90 / 0.95 / 0.99 with the false-positive rate of each negative set; separation `d'`; AUC (rank statistic); the fraction of each set falling in the current bands; `suggested = {"near_duplicate": thr@0.95, "variant": thr@0.99}` rounded to 3 decimals.
- Output: `paths.root / "reports" / f"eval-{model_id}-{YYYYmmddTHHMMSSZ}.json"` and `.md`; the result dict contains the two paths, counts and `suggested`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_eval_job.py`:

```python
import json
import sqlite3
import uuid

import numpy as np
import pytest

from tests.fixtures.v1db import SCHEMA
from visualstore.db import files, fingerprints, folders, pairs
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
    arch = folders.create_folder(conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all")
    inbox = folders.create_folder(conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    rng = np.random.default_rng(7)
    v1 = sqlite3.connect(tmp_path / "v1.db")
    v1.executescript(SCHEMA)
    ids = []
    for i in range(60):
        base = rng.normal(size=512)
        a, b = uuid.uuid4(), uuid.uuid4()
        for u, folder, noise in ((a, arch, 0.0), (b, inbox, 0.02)):
            fid = files.insert_file(conn, folder_id=folder, directory="", name=f"{u}.jpg", extension_id=ext, size=1, mtime=1)
            # keep the v1 id as our id so the v1 rows map onto our rows
            conn.execute("UPDATE file SET id = ? WHERE id = ?", (u.bytes, fid))
            conn.commit()
            files.set_fingerprint_result(conn, u.bytes, sha1=bytes([i % 250]) * 20 if noise == 0 else bytes([200 + i % 50]) * 20, state="fingerprinted")
            fingerprints.replace_frames(vconn, u.bytes, MODEL, [(0, 0, unit(base + rng.normal(scale=noise, size=512)))])
        ids.append((a, b))
    for a, b in ids[:40]:  # related pairs with a small distance
        v1.execute("INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().bytes_le, a.bytes_le, b.bytes_le, 0.01, 1))
    for (a, _), (_, b) in zip(ids[:30], ids[30:60], strict=True):  # unrelated: different bases
        v1.execute("INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().bytes_le, a.bytes_le, b.bytes_le, 0.5, 2))
    v1.execute("INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().bytes_le, uuid.uuid4().bytes_le, ids[0][0].bytes_le, 0.1, 1))  # orphan
    v1.commit()
    v1.close()
    yield reg, conn, tmp_path
    reg.close()


def ctx_for(reg, params):
    conn = reg.conn("a")
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path,
                     settings=settings_db.all(conn), params=params, report=lambda p: None, model_dir=reg.data_dir / "models")


def test_eval_separates_positives_from_negatives_and_writes_reports(world):
    """Review Focus 5."""
    reg, conn, tmp_path = world
    before = conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0]
    result = run_eval(ctx_for(reg, {"db_path": str(tmp_path / "v1.db"), "negatives": 200, "seed": 1}))
    assert result["positives"] >= 40 and result["negatives_labelled"] == 30 and result["negatives_random"] == 200
    assert result["orphans"] == 1
    assert result["suggested"]["near_duplicate"] < result["suggested"]["variant"] < 0.5
    assert result["fpr_at_recall"]["0.95"]["labelled"] == 0.0 and result["fpr_at_recall"]["0.95"]["random"] == 0.0
    assert result["auc"] > 0.99
    report_json = tmp_path / "data" / "profiles" / "a" / "reports"
    assert len(list(report_json.glob("eval-*.json"))) == 1 and len(list(report_json.glob("eval-*.md"))) == 1
    data = json.loads(next(report_json.glob("eval-*.json")).read_text())
    assert data["model_id"] == MODEL and data["bands_current"]["positives"]["near_duplicate"] > 0.9
    assert conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0] == before  # nothing written
    assert IO_HANDLERS["eval"] is run_eval


def test_eval_refuses_without_enough_labelled_positives(world):
    reg, conn, tmp_path = world
    v1 = sqlite3.connect(tmp_path / "v1.db")
    v1.execute("DELETE FROM SimilarFile WHERE IgnoredMode = 1")
    v1.commit()
    v1.close()
    # sha1-equal pairs still exist for the archive files (same sha1 per i) but fewer than 20 distinct pairs? ensure not:
    conn.execute("UPDATE file SET sha1 = randomblob(20)")
    conn.commit()
    with pytest.raises(EvalError, match="positive"):
        run_eval(ctx_for(reg, {"db_path": str(tmp_path / "v1.db"), "negatives": 10}))
    with pytest.raises(EvalError):
        run_eval(ctx_for(reg, {"db_path": str(tmp_path / "missing.db")}))
```

Run: `uv run pytest tests/integration/test_eval_job.py -q` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement**

`visualstore/jobs/io/eval.py`:

```python
"""Model / threshold evaluation against v1 judgments (design §7.6). Read-only; writes a report."""

from __future__ import annotations

import json
import random
import uuid
from contextlib import closing
from datetime import UTC, datetime

import numpy as np

from visualstore.db import fingerprints as fp
from visualstore.domain.bands import BANDS, band, thresholds_from
from visualstore.jobs.io.import_v1 import _open_v1, gid
from visualstore.jobs.scheduler import IoContext
from visualstore.models.registry import get_spec

MIN_POSITIVES = 20
RECALLS = (0.90, 0.95, 0.99)


class EvalError(Exception):
    pass


def _load_vectors(vconn, model_id: str, ids: set[bytes]) -> dict[bytes, np.ndarray]:
    out: dict[bytes, np.ndarray] = {}
    for fid in ids:
        rows = fp.frames_of_file(vconn, fid, model_id)
        if rows:
            out[fid] = np.frombuffer(rows[0]["vector"], dtype=np.float16).astype(np.float32)
    return out


def _distances(pairs_: list[tuple[bytes, bytes]], vectors: dict[bytes, np.ndarray]) -> np.ndarray:
    return np.array([1.0 - float(np.dot(vectors[a], vectors[b])) for a, b in pairs_], dtype=np.float64)


def _stats(d: np.ndarray) -> dict:
    if d.size == 0:
        return {"count": 0}
    hist, _ = np.histogram(np.clip(d, 0, 1), bins=20, range=(0.0, 1.0))
    return {
        "count": int(d.size), "mean": round(float(d.mean()), 4), "median": round(float(np.median(d)), 4),
        "p05": round(float(np.quantile(d, 0.05)), 4), "p95": round(float(np.quantile(d, 0.95)), 4),
        "histogram": hist.tolist(),
    }


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Probability that a random positive is closer than a random negative (rank statistic)."""
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    all_d = np.concatenate([pos, neg])
    ranks = np.argsort(np.argsort(all_d)) + 1  # 1-based ranks, smaller distance = smaller rank
    pos_ranks = ranks[: pos.size].sum()
    return float(1.0 - (pos_ranks - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def run_eval(ctx: IoContext) -> dict:
    spec = get_spec(ctx.settings["model.id"])
    seed = int(ctx.params.get("seed", 1))
    want_random = int(ctx.params.get("negatives", 5000))
    rnd = random.Random(seed)
    with (
        closing(ctx.open_metadata()) as conn,
        closing(ctx.open_vectors()) as vconn,
        closing(_open_v1_or_error(ctx.params["db_path"])) as v1,
    ):
        have = {bytes(r["id"]) for r in conn.execute("SELECT id FROM file WHERE state = 'fingerprinted'")}
        positives: set[tuple[bytes, bytes]] = set()
        negatives: set[tuple[bytes, bytes]] = set()
        orphans = 0
        for row in v1.execute("SELECT File1Id, File2Id, IgnoredMode FROM SimilarFile WHERE IgnoredMode IN (1, 2)"):
            a, b = gid(row["File1Id"]), gid(row["File2Id"])
            if a not in have or b not in have or a == b:
                orphans += 1
                continue
            (positives if row["IgnoredMode"] == 1 else negatives).add(tuple(sorted((a, b))))
        sha_pairs: list[tuple[bytes, bytes]] = []
        for bucket in conn.execute(
            "SELECT group_concat(hex(id), ',') AS ids FROM file WHERE sha1 IS NOT NULL"
            " AND state = 'fingerprinted' GROUP BY sha1 HAVING COUNT(*) > 1"
        ):
            members = [bytes.fromhex(h) for h in bucket["ids"].split(",")]
            sha_pairs.extend(tuple(sorted((members[0], m))) for m in members[1:])
        rnd.shuffle(sha_pairs)
        positives.update(sha_pairs[:20_000])
        ctx.report({"done": 0, "total": 0, "message": f"{len(positives)} positives, {len(negatives)} labelled negatives"})

        by_folder: dict[bytes, list[bytes]] = {}
        for r in conn.execute("SELECT id, folder_id FROM file WHERE state = 'fingerprinted'"):
            by_folder.setdefault(bytes(r["folder_id"]), []).append(bytes(r["id"]))
        folders_with_files = [f for f, ids in by_folder.items() if ids]
        random_negatives: set[tuple[bytes, bytes]] = set()
        attempts = 0
        while len(folders_with_files) >= 2 and len(random_negatives) < want_random and attempts < want_random * 20:
            attempts += 1
            fa, fb = rnd.sample(folders_with_files, 2)
            pair = tuple(sorted((rnd.choice(by_folder[fa]), rnd.choice(by_folder[fb]))))
            if pair not in positives:
                random_negatives.add(pair)

        needed = {f for p in positives | negatives | random_negatives for f in p}
        vectors = _load_vectors(vconn, spec.id, needed)
        keep = lambda s: [p for p in s if p[0] in vectors and p[1] in vectors]  # noqa: E731
        pos, neg_lab, neg_rnd = keep(positives), keep(negatives), keep(random_negatives)
        if len(pos) < MIN_POSITIVES:
            raise EvalError(
                f"only {len(pos)} labelled positive pairs have vectors for {spec.id}; at least {MIN_POSITIVES} are needed"
            )
        d_pos, d_lab, d_rnd = _distances(pos, vectors), _distances(neg_lab, vectors), _distances(neg_rnd, vectors)
        d_neg = np.concatenate([d_lab, d_rnd]) if d_lab.size or d_rnd.size else np.array([])

        fpr_at_recall = {}
        for r in RECALLS:
            thr = float(np.quantile(d_pos, r))
            fpr_at_recall[f"{r:.2f}"] = {
                "threshold": round(thr, 4),
                "labelled": round(float((d_lab <= thr).mean()), 4) if d_lab.size else None,
                "random": round(float((d_rnd <= thr).mean()), 4) if d_rnd.size else None,
            }
        t = thresholds_from(ctx.settings)
        bands_current = {
            name: {b: round(float(np.mean([band(x, False, t) == b for x in d])), 4) if d.size else None for b in BANDS[1:]}
            for name, d in (("positives", d_pos), ("negatives_labelled", d_lab), ("negatives_random", d_rnd))
        }
        pooled = np.sqrt((d_pos.var() + (d_neg.var() if d_neg.size else 0)) / 2) or 1.0
        summary = {
            "model_id": spec.id, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "positives": len(pos), "negatives_labelled": len(neg_lab), "negatives_random": len(neg_rnd),
            "orphans": orphans, "distances": {"positives": _stats(d_pos), "negatives_labelled": _stats(d_lab), "negatives_random": _stats(d_rnd)},
            "fpr_at_recall": fpr_at_recall, "separation_dprime": round(float((d_neg.mean() - d_pos.mean()) / pooled), 3) if d_neg.size else None,
            "auc": round(_auc(d_pos, d_neg), 4) if d_neg.size else None,
            "bands_current": bands_current, "thresholds_current": {"near_duplicate": t.near_duplicate, "variant": t.variant},
            "suggested": {"near_duplicate": fpr_at_recall["0.95"]["threshold"], "variant": fpr_at_recall["0.99"]["threshold"]},
        }
        reports = ctx.paths.root / "reports"
        reports.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = reports / f"eval-{spec.id}-{stamp}.json"
        md_path = reports / f"eval-{spec.id}-{stamp}.md"
        json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        md_path.write_text(_markdown(summary), encoding="utf-8")
        summary["report_json"] = str(json_path)
        summary["report_md"] = str(md_path)
        return summary


def _open_v1_or_error(path: str):
    from visualstore.jobs.io.import_v1 import ImportError_

    try:
        return _open_v1(path)
    except ImportError_ as exc:
        raise EvalError(str(exc)) from exc


def _markdown(s: dict) -> str:
    lines = [
        f"# Evaluation of {s['model_id']}", "", f"Generated {s['generated_at']}.", "",
        f"- Positive pairs (v1 related + byte-identical): {s['positives']}",
        f"- Labelled negative pairs (v1 unrelated): {s['negatives_labelled']}",
        f"- Random cross-Folder negative pairs: {s['negatives_random']}",
        f"- v1 rows skipped (missing files): {s['orphans']}", "",
        "## Distances", "", "| set | count | mean | median | p05 | p95 |", "|---|---|---|---|---|---|",
    ]
    for name, st in s["distances"].items():
        if st.get("count"):
            lines.append(f"| {name} | {st['count']} | {st['mean']} | {st['median']} | {st['p05']} | {st['p95']} |")
    lines += ["", "## False-positive rate at fixed recall", "", "| recall | threshold | labelled negatives | random negatives |", "|---|---|---|---|"]
    for r, v in s["fpr_at_recall"].items():
        lines.append(f"| {r} | {v['threshold']} | {v['labelled']} | {v['random']} |")
    lines += ["", f"Separation d' = {s['separation_dprime']}, AUC = {s['auc']}.", "",
              f"Current thresholds: near_duplicate {s['thresholds_current']['near_duplicate']}, variant {s['thresholds_current']['variant']}.",
              f"Suggested: near_duplicate {s['suggested']['near_duplicate']} (95 % recall), variant {s['suggested']['variant']} (99 % recall).", ""]
    return "\n".join(lines)
```

Register `"eval": run_eval` in `IO_HANDLERS` and `"eval": "io"` in `KIND_OF`.

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/jobs/io/eval.py visualstore/jobs/io/__init__.py visualstore/jobs/types.py tests/integration/test_eval_job.py
git commit -m "feat: evaluation job against v1 judgments with a written report

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: `model_switch` io job

**Files:**
- Create: `visualstore/jobs/io/model_switch.py`, `tests/integration/test_model_switch.py`
- Modify: `visualstore/jobs/io/__init__.py`, `visualstore/jobs/types.py`

**Interfaces:**
- Io job `model_switch`, params `{"model_id"}`. `run_model_switch(ctx) -> dict` → `{"model_id", "queued_files", "fingerprint_jobs", "rebuild_queued"}`. Steps: `get_spec` (ModelError → job fails, nothing changed); `resolve_model_path(spec, ctx.model_dir)` (download allowed; failure → nothing changed); `settings.set(conn, "model.id", spec.id)`; for every Folder, image files with `state = fingerprinted` lacking vectors for `spec.id` → `fingerprint` jobs (batches of 64, role priority, `model` = new model params); enqueue `rebuild_index` once unless active. Judgments, pairs and groups are untouched (Review Focus 3).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_model_switch.py`:

```python
import json

import numpy as np
import pytest

from visualstore.db import files, fingerprints, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.model_switch import run_model_switch
from visualstore.jobs.scheduler import IoContext
from visualstore.models.registry import ModelError
from visualstore.profiles import ProfileRegistry

OLD, NEW = "sscd-resnet50-512-v1", "dinov2-vits14-384-v1"


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / f"{NEW}.onnx").write_bytes(b"pretend")  # local-only: hash unknown, file present

    def add(name, media="jpg", with_new=False):
        fid = files.insert_file(conn, folder_id=folder, directory="", name=name, extension_id=ext[media]["id"], size=1, mtime=1)
        files.set_fingerprint_result(conn, fid, sha1=bytes([len(name)]) * 20, state="fingerprinted")
        fingerprints.replace_frames(vconn, fid, OLD, [(0, 0, np.zeros(512, np.float16).tobytes())])
        if with_new:
            fingerprints.replace_frames(vconn, fid, NEW, [(0, 0, np.zeros(384, np.float16).tobytes())])
        return fid

    yield reg, conn, vconn, model_dir, add
    reg.close()


def ctx_for(reg, model_dir, params):
    conn = reg.conn("a")
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path,
                     settings=settings_db.all(conn), params=params, report=lambda p: None, model_dir=model_dir)


def test_switch_queues_only_missing_vectors_and_keeps_judgments(world):
    """Review Focus 3."""
    reg, conn, vconn, model_dir, add = world
    a, b, c = add("a.jpg"), add("b.jpg", with_new=True), add("c.mp4", media="mp4")
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.set_judgment(conn, a, b, "related_keep", "user")
    groups.replace_groups(conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "resolved", {a: "keep", b: "keep"})])
    result = run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    assert result["model_id"] == NEW and result["queued_files"] == 1 and result["rebuild_queued"] is True
    assert settings_db.get(conn, "model.id") == NEW
    queued = jobs_db.list_jobs(conn, state="queued")
    fps = [j for j in queued if j["type"] == "fingerprint"]
    assert len(fps) == 1
    params = json.loads(fps[0]["params"])
    assert [f["id"] for f in params["files"]] == [id_to_str(a)] and params["model"]["id"] == NEW
    assert any(j["type"] == "rebuild_index" for j in queued)
    assert pairs.pairs_of_file(conn, a)[0]["judgment"] == "related_keep"
    assert groups.group_of_file(conn, a)["status"] == "resolved"
    assert fingerprints.vector_count(vconn, OLD) == 3  # old vectors kept (design §7.7)
    assert IO_HANDLERS["model_switch"] is run_model_switch


def test_unknown_or_unavailable_model_changes_nothing(world):
    reg, conn, vconn, model_dir, add = world
    add("a.jpg")
    with pytest.raises(ModelError):
        run_model_switch(ctx_for(reg, model_dir, {"model_id": "nope"}))
    (model_dir / f"{NEW}.onnx").unlink()
    with pytest.raises(ModelError, match="not downloadable"):
        run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    assert settings_db.get(conn, "model.id") == OLD
    assert jobs_db.list_jobs(conn) == []
```

Run: `uv run pytest tests/integration/test_model_switch.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/jobs/io/model_switch.py`:

```python
"""Switch the active model (design §7.7): queue vectors for files that lack them, rebuild the index."""

from __future__ import annotations

from contextlib import closing

from visualstore.db import fingerprints as fp
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.scan import FINGERPRINT_BATCH, _abs
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX
from visualstore.models.registry import get_spec, job_params, resolve_model_path


def run_model_switch(ctx: IoContext) -> dict:
    spec = get_spec(ctx.params["model_id"])
    resolve_model_path(spec, ctx.model_dir)  # downloads when allowed; raises before any change
    model = job_params(spec, ctx.model_dir)
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        settings_db.set(conn, "model.id", spec.id)
        have = fp.files_with_vectors(vconn, spec.id)
        queued_files = jobs_made = 0
        for folder in folders_db.list_folders(conn):
            rows = conn.execute(
                "SELECT f.id, f.directory, f.name FROM file f JOIN extension e ON e.id = f.extension_id"
                " WHERE f.folder_id = ? AND f.state = 'fingerprinted' AND e.media_type = 'image'"
                " ORDER BY f.directory, f.name",
                (bytes(folder["id"]),),
            ).fetchall()
            pending = [r for r in rows if bytes(r["id"]) not in have]
            priority = PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE
            for start in range(0, len(pending), FINGERPRINT_BATCH):
                batch = pending[start : start + FINGERPRINT_BATCH]
                jobs_db.enqueue(
                    conn, type="fingerprint", kind="compute", priority=priority,
                    params={
                        "files": [{"id": id_to_str(r["id"]), "path": _abs(folder["path"], r["directory"], r["name"]), "media_type": "image"} for r in batch],
                        "model": model, "max_pixels": int(ctx.settings["media.max_pixels"]), "priority": priority,
                    },
                )
                jobs_made += 1
                queued_files += len(batch)
        rebuild = not (jobs_db.has_active(conn, "rebuild_index") or jobs_db.has_active(conn, "build_index"))
        if rebuild:
            jobs_db.enqueue(conn, type="rebuild_index", kind="io", priority=PRIORITY_INBOX)
        return {"model_id": spec.id, "queued_files": queued_files, "fingerprint_jobs": jobs_made, "rebuild_queued": rebuild}
```

Register `"model_switch"` in `IO_HANDLERS` and `KIND_OF` (`io`).

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/jobs/io/model_switch.py visualstore/jobs/io/__init__.py visualstore/jobs/types.py tests/integration/test_model_switch.py
git commit -m "feat: model switch job

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Models, evaluation and thumbnail API

**Files:**
- Create: `visualstore/api/models.py`, `tests/integration/test_api_similarity.py`
- Modify: `visualstore/api/files.py`, `visualstore/app.py`

**Interfaces** (auth required; profile-scoped ones use `get_profile`):
- `GET /api/models` → `[{id, dim, input_size, description, available}]` (`available` = file present in `config.model_dir`).
- `POST /api/p/{name}/model-switch {model_id}` → 400 for unknown ids; 201 `model_switch` job at `PRIORITY_USER`.
- `POST /api/p/{name}/eval {db_path, negatives?: int}` → 201 `eval` job at `PRIORITY_USER`.
- `GET /api/p/{name}/eval/reports` → `[{file, size, time}]` newest first; `GET /api/p/{name}/eval/reports/{file}` → the report (`text/markdown` or `application/json`); file names must match `^eval-[A-Za-z0-9._-]+\.(md|json)$` (404 otherwise).
- `GET /api/p/{name}/files/{file_id}/thumbnail` → webp `FileResponse`, 404 when absent.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_api_similarity.py`:

```python
from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders
from visualstore.ids import id_to_str


def setup(client):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201


def test_models_listing_and_switch_validation(client, config):
    setup(client)
    listed = client.get("/api/models").json()
    by_id = {m["id"]: m for m in listed}
    assert by_id["sscd-resnet50-512-v1"]["dim"] == 512 and by_id["sscd-resnet50-512-v1"]["available"] is False
    config.model_dir.mkdir(parents=True, exist_ok=True)
    (config.model_dir / "dinov2-vits14-384-v1.onnx").write_bytes(b"x")
    assert {m["id"]: m["available"] for m in client.get("/api/models").json()}["dinov2-vits14-384-v1"] is True
    assert client.post("/api/p/a/model-switch", json={"model_id": "nope"}).status_code == 400
    job = client.post("/api/p/a/model-switch", json={"model_id": "dinov2-vits14-384-v1"})
    assert job.status_code == 201
    finished = wait_for(client, "a", job.json()["id"])
    assert finished["state"] == "done" and finished["progress"]["result"]["model_id"] == "dinov2-vits14-384-v1"
    assert client.get("/api/p/a/settings").json()["model.id"] == "dinov2-vits14-384-v1"


def test_eval_endpoints(client, tmp_path):
    setup(client)
    assert client.get("/api/p/a/eval/reports").json() == []
    job = client.post("/api/p/a/eval", json={"db_path": str(tmp_path / "missing.db")})
    assert job.status_code == 201
    finished = wait_for(client, "a", job.json()["id"])
    assert finished["state"] == "failed" and "not found" in finished["error"]
    reports = tmp_path / "data" / "profiles" / "a" / "reports"
    reports.mkdir()
    (reports / "eval-sscd-resnet50-512-v1-20261106T000000Z.md").write_text("# report")
    [entry] = client.get("/api/p/a/eval/reports").json()
    assert entry["file"].startswith("eval-")
    got = client.get(f"/api/p/a/eval/reports/{entry['file']}")
    assert got.status_code == 200 and got.text == "# report"
    assert client.get("/api/p/a/eval/reports/../../global.db").status_code == 404
    assert client.get("/api/p/a/eval/reports/other.txt").status_code == 404


def test_thumbnail_endpoint(client, tmp_path):
    setup(client)
    conn = client.app.state.vs.registry.conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(conn, folder_id=folder, directory="", name="a.jpg", extension_id=ext, size=1, mtime=1)
    text = id_to_str(fid)
    assert client.get(f"/api/p/a/files/{text}/thumbnail").status_code == 404
    thumbs = tmp_path / "data" / "profiles" / "a" / "thumbs" / text[:2]
    thumbs.mkdir(parents=True)
    (thumbs / f"{text}.webp").write_bytes(b"RIFF....WEBPVP8 ")
    got = client.get(f"/api/p/a/files/{text}/thumbnail")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
```

Run: `uv run pytest tests/integration/test_api_similarity.py -q` — Expected: FAIL (404s).

- [ ] **Step 2: Implement**

`visualstore/api/models.py`:

```python
"""Model registry, model switch and evaluation endpoints (design §7.6, §7.7)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import PRIORITY_USER
from visualstore.models.registry import load_registry

router = APIRouter(tags=["models"], dependencies=[Depends(require_auth)])
_REPORT_NAME = re.compile(r"^eval-[A-Za-z0-9._-]+\.(md|json)$")


class SwitchModel(BaseModel):
    model_id: str


class RunEval(BaseModel):
    db_path: str
    negatives: int = 5000


@router.get("/api/models")
async def list_models(request: Request) -> list[dict]:
    model_dir = get_state(request).config.model_dir
    return [
        {"id": s.id, "dim": s.dim, "input_size": s.input_size, "description": s.description,
         "available": (model_dir / s.file_name()).is_file()}
        for s in load_registry().values()
    ]


@router.post("/api/p/{name}/model-switch", status_code=201)
async def switch_model(body: SwitchModel, request: Request, name: str = Depends(get_profile)) -> dict:
    if body.model_id not in load_registry():
        raise HTTPException(status_code=400, detail=f"Unknown model '{body.model_id}'.")
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(conn, type="model_switch", kind="io", priority=PRIORITY_USER, params={"model_id": body.model_id})
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/api/p/{name}/eval", status_code=201)
async def run_eval(body: RunEval, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    job_id = jobs_db.enqueue(conn, type="eval", kind="io", priority=PRIORITY_USER, params=body.model_dump())
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.get("/api/p/{name}/eval/reports")
async def list_reports(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    reports = get_state(request).registry.paths(name).root / "reports"
    if not reports.is_dir():
        return []
    out = [
        {"file": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime}
        for p in reports.iterdir() if _REPORT_NAME.match(p.name)
    ]
    out.sort(key=lambda d: d["time"], reverse=True)
    return out


@router.get("/api/p/{name}/eval/reports/{file}")
async def get_report(file: str, request: Request, name: str = Depends(get_profile)) -> FileResponse:
    if not _REPORT_NAME.match(file):
        raise HTTPException(status_code=404, detail="No such report.")
    path = get_state(request).registry.paths(name).root / "reports" / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such report.")
    media = "application/json" if file.endswith(".json") else "text/markdown"
    return FileResponse(path, media_type=media)
```

`visualstore/api/files.py`: add

```python
@router.get("/{file_id}/thumbnail")
async def file_thumbnail(file_id: str, request: Request, name: str = Depends(get_profile)) -> FileResponse:
    parse_id(file_id)
    path = get_state(request).registry.paths(name).thumbs / file_id[:2] / f"{file_id}.webp"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No thumbnail yet.")
    return FileResponse(path, media_type="image/webp")
```

`app.py` includes `models_api.router`.

- [ ] **Step 3: Run the tests, then commit**

```bash
git add visualstore/api/models.py visualstore/api/files.py visualstore/app.py tests/integration/test_api_similarity.py
git commit -m "feat: models, model switch, evaluation and thumbnail API

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Frontend — thumbnails, bands, model and evaluation controls

**Files:**
- Modify: `web/src/types.ts`, `web/src/pages/Groups.tsx`, `web/src/pages/GroupDetail.tsx`, `web/src/pages/Jobs.tsx`, `web/src/styles.css`

**Interfaces:**
- `Group.band: "identical" | "near_duplicate" | "variant" | "candidate"`; `ModelInfo {id, dim, input_size, description, available}`; `EvalReport {file, size, time}`.
- Groups page: thumbnail of the first member (`<img className="thumb">` from `/api/p/{name}/files/{id}/thumbnail`, hidden on error), a `band` column, and a band filter (`any / identical / near_duplicate / variant / candidate`); the counts line shows per-band open counts.
- GroupDetail: a thumbnail per member next to its path; the band tag in the heading.
- Jobs page: a **Model** section (`GET /api/models`, select with "(available)" markers, "Switch model" → `POST /model-switch`), an **Evaluation** section (v1 db path, negatives, "Run evaluation" → `POST /eval`; report list from `GET /eval/reports` with links that open the report in a new tab). The threshold settings already render through the generic settings table.

- [ ] **Step 1: Implement**

`types.ts` additions:

```ts
export type Band = "identical" | "near_duplicate" | "variant" | "candidate";
// add to Group:  band: Band;
export interface ModelInfo {
  id: string;
  dim: number;
  input_size: number;
  description: string;
  available: boolean;
}
export interface EvalReport {
  file: string;
  size: number;
  time: number;
}
```

`Groups.tsx`: add state `const [bandFilter, setBandFilter] = useState("")`, include `band` in the query (`if (bandFilter) q.set("band", bandFilter)`, dependency array extended), a `<label>Band<select>…</select></label>` with the five options, a `<th>Band</th>` column rendering `g.band.replace("_", " ")`, the counts line extended with `· bands: {Object.entries((counts as Record<string, unknown>).bands ?? {}).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ")}` — type `counts` as `Record<string, number | Record<string, number>>` and read `bands` safely — and the first cell becomes:

```tsx
<td className="with-thumb">
  <Thumb name={name} fileId={g.members[0]?.file_id} />
  <Link to={`/p/${name}/groups/${g.id}`}>{g.member_count} files</Link>
  {g.has_identical && <span className="tag">identical</span>}
</td>
```

Create `web/src/components/Thumb.tsx`:

```tsx
import { useState } from "react";

export default function Thumb({ name, fileId, size = 48 }: { name: string; fileId?: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (!fileId || failed) return <span className="thumb thumb-empty" style={{ width: size, height: size }} />;
  return (
    <img
      className="thumb"
      width={size}
      height={size}
      alt=""
      src={`/api/p/${name}/files/${fileId}/thumbnail`}
      onError={() => setFailed(true)}
    />
  );
}
```

`GroupDetail.tsx`: import `Thumb`; in each member's first `<div>` prepend `<Thumb name={name} fileId={m.file_id} size={64} />`; heading gains `<span className="tag">{group.band.replace("_", " ")}</span>`.

`Jobs.tsx`: state `models: ModelInfo[]`, `chosenModel: string`, `reports: EvalReport[]`, `evalForm: {db_path: string; negatives: number}`; `load` also fetches `/api/models` and `/api/p/${name}/eval/reports`; after the Settings section add:

```tsx
<h2>Model</h2>
<p className="muted">Active: <code>{String(settings["model.id"] ?? "")}</code></p>
<form className="inline" onSubmit={(e) => { e.preventDefault(); void run(async () => { const job = await post<Job>(`/api/p/${name}/model-switch`, { model_id: chosenModel }); setNotice(`Model switch queued as job ${job.id}.`); }); }}>
  <label>
    Model
    <select value={chosenModel} onChange={(e) => setChosenModel(e.target.value)} required>
      <option value="">choose</option>
      {models.map((m) => (
        <option key={m.id} value={m.id}>{m.id} · {m.dim}-d{m.available ? " (downloaded)" : ""}</option>
      ))}
    </select>
  </label>
  <button type="submit" disabled={!chosenModel}>Switch model</button>
</form>

<h2>Evaluation against an ImageStore (v1) database</h2>
<form className="inline" onSubmit={(e) => { e.preventDefault(); void run(async () => { const job = await post<Job>(`/api/p/${name}/eval`, evalForm); setNotice(`Evaluation queued as job ${job.id}.`); }); }}>
  <label>
    v1 .db path in the container
    <input value={evalForm.db_path} onChange={(e) => setEvalForm({ ...evalForm, db_path: e.target.value })} placeholder="/import/imagestore.db" required />
  </label>
  <label>
    Random negative pairs
    <input type="number" min={100} value={evalForm.negatives} onChange={(e) => setEvalForm({ ...evalForm, negatives: Number(e.target.value) || 5000 })} />
  </label>
  <button type="submit">Run evaluation</button>
</form>
{reports.length > 0 && (
  <ul>
    {reports.map((r) => (
      <li key={r.file}>
        <a href={`/api/p/${name}/eval/reports/${r.file}`} target="_blank" rel="noreferrer">{r.file}</a>{" "}
        <span className="muted">({formatBytes(r.size)})</span>
      </li>
    ))}
  </ul>
)}
```

`styles.css`: `.thumb { object-fit: cover; border-radius: 0.25rem; vertical-align: middle; margin-right: 0.5rem; background: color-mix(in srgb, currentColor 10%, transparent); } .thumb-empty { display: inline-block; } td.with-thumb { white-space: nowrap; }`.

- [ ] **Step 2: Verify and commit**

Run: `cd web && npm run lint && npm test && npm run build && npm run e2e` — Expected: clean; both e2e tests still pass (thumbnails simply do not load without a model).

```bash
git add web/src
git commit -m "feat(web): thumbnails, distance bands, model switch and evaluation controls

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Export the models, publish the artefacts, pin the hashes, smoke-test the real model

**Files:**
- Create: `scripts/export_models.py`, `tests/integration/test_real_model.py`
- Modify: `visualstore/models/models.json` (sha256 values), `README.md` (model download / offline section), `docs/plans/README.md`, `docs/design/00-overview.md` (§1.3 note on where the measured numbers come from)

**Interfaces:**
- `scripts/export_models.py --out DIR [--only sscd|dinov2]`: downloads the official SSCD TorchScript checkpoint (`https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt`) and the DINOv2 ViT-S/14 weights through `torch.hub` (`facebookresearch/dinov2`, `dinov2_vits14`), exports both to ONNX (opset 17, dynamic batch, input `input[N,3,H,W]`, output `output[N,dim]`, L2 normalisation **not** baked in — the Session normalises), checks each exported model against the PyTorch output on a random batch (max abs diff < 1e-3), and prints `name sha256 size`.
- The script runs with `uv run --no-project --index https://download.pytorch.org/whl/cpu --with torch --with torchvision --with onnx --with onnxscript python scripts/export_models.py --out /tmp/vs-models` (CPU wheels; nothing is added to the project's dependencies).
- `tests/integration/test_real_model.py` runs only with `VISUALSTORE_TEST_REAL_MODEL=1` and `VISUALSTORE_MODEL_DIR` pointing at the exported files: two generated images (a gradient and the same gradient re-encoded at 90 % scale) must land within distance 0.15 of each other with SSCD, and a solid colour image must be farther from both than they are from each other.
- Publishing: `gh release create models-v1 /tmp/vs-models/*.onnx --title "Model artefacts v1" --notes "ONNX exports of SSCD ResNet-50 (disc_mixup) and DINOv2 ViT-S/14 for VisualStore. See scripts/export_models.py."` — **this publishes files to GitHub; confirm with the user before running it.** Then paste the printed sha256 values into `models.json`, run `test_real_model.py` once against the downloaded files, and commit.

- [ ] **Step 1: Write the export script**

`scripts/export_models.py`:

```python
"""One-off export of the candidate models to ONNX (ADR-0005). Needs torch + torchvision (CPU).

    uv run --no-project --index https://download.pytorch.org/whl/cpu \
        --with torch --with torchvision --with onnx --with onnxscript \
        python scripts/export_models.py --out /tmp/vs-models
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

import numpy as np
import torch

SSCD_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class Dinov2Cls(torch.nn.Module):
    def __init__(self, backbone: torch.nn.Module) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # CLS embedding, 384-d for ViT-S/14
        return self.backbone(x)


def export(model: torch.nn.Module, size: int, out: Path) -> None:
    model.eval()
    example = torch.randn(2, 3, size, size)
    torch.onnx.export(
        model, example, str(out), opset_version=17, input_names=["input"], output_names=["output"],
        dynamic_axes={"input": {0: "N"}, "output": {0: "N"}}, dynamo=False,
    )
    import onnxruntime as ort

    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    with torch.no_grad():
        expected = model(example).numpy()
    got = session.run(None, {"input": example.numpy()})[0]
    diff = float(np.abs(expected - got).max())
    if diff > 1e-3:
        sys.exit(f"{out.name}: ONNX output differs from PyTorch by {diff}")
    print(f"{out.name} sha256={sha256(out)} size={out.stat().st_size} max_abs_diff={diff:.2e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", choices=["sscd", "dinov2"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "sscd"):
        ckpt = args.out / "sscd_disc_mixup.torchscript.pt"
        if not ckpt.exists():
            urllib.request.urlretrieve(SSCD_URL, ckpt)  # noqa: S310
        sscd = torch.jit.load(str(ckpt), map_location="cpu")
        export(sscd, 288, args.out / "sscd-resnet50-512-v1.onnx")
    if args.only in (None, "dinov2"):
        backbone = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
        export(Dinov2Cls(backbone), 224, args.out / "dinov2-vits14-384-v1.onnx")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the export**

Run the `uv run --no-project --index https://download.pytorch.org/whl/cpu ... python scripts/export_models.py --out /tmp/vs-models` command. Expected: two lines `sscd-resnet50-512-v1.onnx sha256=… max_abs_diff=…` and `dinov2-vits14-384-v1.onnx sha256=… …`. If the SSCD TorchScript module refuses the legacy exporter, retry with `dynamo=True`; if DINOv2's hub load needs `xformers`, set `XFORMERS_DISABLED=1`. Record whichever path worked in ADR-0005.

- [ ] **Step 3: Write and run the real-model smoke test**

`tests/integration/test_real_model.py`:

```python
import os
from pathlib import Path

import numpy as np
import pytest

from tests.fixtures.images import png_bytes

pyvips = pytest.importorskip("pyvips")
pytestmark = pytest.mark.skipif(
    os.environ.get("VISUALSTORE_TEST_REAL_MODEL") != "1", reason="set VISUALSTORE_TEST_REAL_MODEL=1 and VISUALSTORE_MODEL_DIR"
)


def test_sscd_puts_a_rescaled_copy_close_and_a_different_image_far(tmp_path):
    from visualstore.media.decode import load_image, to_model_input
    from visualstore.models.infer import Session
    from visualstore.models.registry import get_spec, resolve_model_path

    spec = get_spec("sscd-resnet50-512-v1")
    session = Session(resolve_model_path(spec, Path(os.environ["VISUALSTORE_MODEL_DIR"])), spec)

    def embed(data: bytes, name: str):
        p = tmp_path / name
        p.write_bytes(data)
        d = load_image(str(p), 100_000_000)
        return session.embed(to_model_input(d, spec.input_size, spec.mean, spec.std))[0]

    original = embed(png_bytes(320, 240, gradient=True), "orig.png")
    rescaled = embed(pyvips.Image.new_from_buffer(png_bytes(320, 240, gradient=True), "").resize(0.9).write_to_buffer(".png"), "small.png")
    solid = embed(png_bytes(320, 240, rgb=(10, 200, 30)), "solid.png")
    d_copy = 1 - float(np.dot(original, rescaled))
    d_other = 1 - float(np.dot(original, solid))
    assert d_copy < 0.15, d_copy
    assert d_other > d_copy * 2, (d_copy, d_other)
```

Run (in the test image, which has libvips): `docker build --target test -t visualstore:test . && docker run --rm -e VISUALSTORE_TEST_REAL_MODEL=1 -e VISUALSTORE_MODEL_DIR=/models -v /tmp/vs-models:/models:ro -v "$PWD/tests:/app/tests" visualstore:test python -m pytest /app/tests/integration/test_real_model.py -q` — Expected: 1 passed.

- [ ] **Step 4: Publish and pin**

Ask the user to confirm the release, then: `gh release create models-v1 /tmp/vs-models/sscd-resnet50-512-v1.onnx /tmp/vs-models/dinov2-vits14-384-v1.onnx --title "Model artefacts v1" --notes "..."`. Fill both `sha256` values in `visualstore/models/models.json`; `test_model_registry.py::test_registry_ships_both_candidates` gains `assert len(s.sha256) == 64`. Delete the local files and run `uv run python -c "from pathlib import Path; from visualstore.models.registry import get_spec, resolve_model_path; print(resolve_model_path(get_spec('sscd-resnet50-512-v1'), Path('/tmp/vs-dl')))"` — Expected: downloads and verifies.

- [ ] **Step 5: Documentation and commit**

README: a "Models" paragraph under Running — first use downloads ~100 MB into `/data/models`; offline installs mount the files and set `VISUALSTORE_MODEL_DIR`; how to run the evaluation and where reports live. `docs/plans/README.md`: milestone 3 → shipped. `docs/design/00-overview.md` §1.3: add the sentence "Measured values are produced by the evaluation job (Jobs → Evaluation) on the user's imported v1 data; the report path is recorded here when the run has been done." — the actual run on the user's library is an operator step the user performs with their data.

```bash
git add scripts/export_models.py tests/integration/test_real_model.py visualstore/models/models.json tests/unit/test_model_registry.py README.md docs/plans/README.md docs/design/00-overview.md docs/decisions/0005-m3-implementation-choices.md
git commit -m "feat: model export script, published artefact hashes, real-model smoke test; docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

Spec coverage for milestone 3 ([11 §13 item 3](../design/11-roadmap.md#13-milestones)): model export and ONNX inference (Tasks 2, 4, 14); vector fingerprints (Tasks 1, 5); thumbnails (Tasks 3, 5, 12, 13); usearch index (Task 6); match job (Task 7); band settings (Tasks 1, 9); eval job and report (Task 10); model lock-in (Task 11 switch job + Task 14 operator step). Rebuild/startup consistency (design §7.4 last paragraph) is Task 8; §7.7 model change semantics are Task 11.

Deviations from the task-level plan, recorded in ADR-0005: plain `seq` keys instead of directory-hash keys (no filtered search); `eval` as an io job; frames deleted explicitly through `open_vectors()`.

Deferred to later milestones on purpose: animated/video frames (M5), image comparison pane (M4), orphan vector sweep (M6), filtered search / top-k crowding measurement (M6), PCA / quantisation (M6).

Names checked across tasks: `fingerprints.replace_frames/allocate_keys/resolve_keys/pending_removals/drop_keys/get_meta/set_meta/iter_vectors/files_with_vectors/vector_count/delete_for_files`; `SinkContext` fields; `registry.get_spec/resolve_model_path/job_params/load_registry`; `VectorIndex.open_or_create/empty/add/remove/search/save/count` and `inspect`; `ScopeInfo/allowed`; `band/thresholds_from/band_bounds/BANDS`; job types `match`, `build_index`, `rebuild_index`, `eval`, `model_switch` in `KIND_OF`, `HANDLERS`, `IO_HANDLERS`, `RESULT_SINKS`; route paths against `Jobs.tsx`/`Groups.tsx`/`GroupDetail.tsx`.
