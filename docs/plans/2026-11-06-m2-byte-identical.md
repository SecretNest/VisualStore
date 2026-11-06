# Milestone 2 · Byte-identical pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VisualStore replaces ImageStore's same-file workflow: Folders, ignored directories and extensions are managed in the browser; scanning finds files and recognises moves; SHA-1 fingerprinting runs in the compute pool; byte-identical files form groups with preselected decisions; a human (or the automatic rule) resolves groups into trash moves; inbox Folders can be moved into an archive Folder; a v1 database imports in one step.

**Architecture:** Milestone 1's skeleton gains the domain tables (migration 0002), pure `domain/` logic (scan diff, move detection, union-find grouping, default resolution rules), io jobs (`scan`, `regroup`, `apply_action`, `archive_move`, `purge_trash`, `import_v1`) that open their own SQLite connection in the scheduler's worker thread, a `fingerprint` compute handler that computes SHA-1 in children, and a **result sink** step in the scheduler that turns compute results into database rows on the event-loop thread (file state, sha1 pairs, dirty marks). The frontend gains Folder management, the review queue, group detail with keyboard resolution, and a jobs/settings page with trash, archive move and v1 import.

**Tech Stack:** As milestone 1 (Python 3.12, FastAPI, sqlite3, React + TypeScript). No new runtime dependencies; SHA-1 is `hashlib`.

**Spec:** `docs/design/` — [01 Requirements](../design/01-requirements.md), [03 Data Model §5.1 Folder/IgnoredDirectory/Extension/File/Pair/Group/Action, §5.3](../design/03-data-model.md), [04 Jobs §6.2–6.4](../design/04-jobs.md), [05 Pipeline §7.1 scan, §7.2 step 1, §7.4 SHA-1 pairs, §7.5 grouping](../design/05-pipeline.md), [06 Review UI §8.1 pages 2–5, §8.2, §8.3 (no image comparison yet), §8.5](../design/06-review-ui.md), [08 v1 Import](../design/08-v1-import.md), [10 Testing](../design/10-testing.md), [11 Roadmap §13 item 2](../design/11-roadmap.md#13-milestones). Decisions made while planning this milestone: [ADR-0004](../decisions/0004-m2-implementation-choices.md) (written in Task 1).

## Global Constraints

Everything in the milestone 1 plan's Global Constraints still holds. In addition:

- Every registry connection is used only on the event-loop thread (async handlers and the scheduler). Io job handlers run in the worker thread and open their own connection with `ctx.open_metadata()`; they never touch registry connections. Both connections share one WAL file, so `connect()` sets a 30 s busy timeout and every transaction is short.
- Compute children receive absolute container paths and return plain data; they never see a database. Compute results are persisted by a per-type **result sink** running on the event-loop thread.
- Paths inside the database are container paths for `folder.path` and `/`-separated relative paths for `file.directory` (empty string for the root) and `ignored_directory.directory`. No `\`, no leading or trailing `/`.
- `(folder_id, directory, name)` is unique with `COLLATE NOCASE`; `file.name` includes the extension (unlike v1). `extension.extension` is lowercase without a dot.
- Folders must not nest, and a Folder path must be an absolute, existing directory inside the container at creation time.
- The scan always skips `.visualstore-trash` at a Folder root, in addition to configured ignored directories.
- `pair.file_a < pair.file_b` (byte comparison of the 16-byte ids). A file is in at most one group.
- Default resolution rules ([06 §8.2](../design/06-review-ui.md#82-default-resolution-rules)) are the only place that decides keep/delete preselection; UI and auto-execution both call them.
- Deletion moves to `<folder.path>/.visualstore-trash/<yyyy-mm>/<directory>/<name>` by default, keeping the relative path; an `action` row records before/after. Permanent deletion only when the profile setting `trash.enabled` is false. Every disk operation happens before its database change; a disk failure leaves the database unchanged and fails the job.
- Trash retention default 30 days (`trash.retention_days`); `purge_trash` runs daily via the planner.
- Priority of jobs that touch files: user-triggered 100 > inbox 50 > archive 20 > maintenance 0 (from `jobs/types.py`).
- v1 import reads the v1 database read-only and preserves v1 ids read with `uuid.UUID(bytes_le=...)`. It requires an empty profile (no Folders). SimilarFile is not imported (the optional switch is milestone 3+ work, [11 §15](../design/11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)).
- UI strings English. Commit messages Conventional Commits with the attribution trailer.

## Review Focus

1. **A Folder path that is the parent, child, or equal of an existing Folder's path** must be rejected (design: Folders must not nest), including paths differing only by a trailing slash. (Task 1 step 1, Task 15 step 1.)
2. **A file renamed or moved on disk between scans** must keep its id, sha1, pairs and judgments; a file whose content changed (same path, different size/mtime) must lose its sha1 and pairs and be fingerprinted again. (Task 2 step 1, Task 9 step 1.)
3. **Trash on a different filesystem, or a name collision in the trash directory** must not lose the file: a cross-device move degrades to copy + verify size + delete, and an existing target gets a numeric suffix. The database row changes only after the disk operation succeeded. (Task 11 step 1.)
4. **Automatic execution of identical groups** must never touch a group whose files are all in a single archive Folder (several archive Folders are decided by priority, design §8.2), must never delete a member a human marked `related_keep`, must not fire when a judgment of any kind exists inside the group, and must be fully off when the setting `resolution.auto_execute_identical` is false. (Task 4 step 1, Task 10 step 1; wording corrected after the final review, see ADR-0004.)
5. **A v1 database with a SimilarFile row pointing at a deleted file, a Folder path mapping that is missing one Folder, or ids written by .NET** must import without crashing, report what it skipped, and produce ids that match v1's textual Guids. (Task 13 step 1.)

---

## File structure

```
visualstore/
  db/
    connection.py                     + timeout=30 (Task 6)
    migrations/profile/0002_domain.sql folder, ignored_directory, extension (+defaults), file, pair,
                                      file_group, file_group_member, regroup_queue, action (Task 1)
    folders.py                        folder / ignored_directory / extension queries (Task 1)
    files.py                          file queries incl. sha1 buckets, state resets (Task 1)
    pairs.py                          pair upsert / judgments / neighbours (Task 5)
    groups.py                         group + member + regroup_queue queries (Task 5)
    actions.py                        action rows (Task 5)
    settings.py                       + trash.*, resolution.*, scan.* defaults (Task 14)
  domain/
    __init__.py
    paths.py                          split/join/normalise relative paths (Task 2)
    scan.py                           diff(disk, db) -> ScanPlan; detect_moves (Task 2)
    grouping.py                       components(pairs, group_distance) (Task 3)
    resolution.py                     preselect(group members) -> decisions, status (Task 4)
  jobs/
    scheduler.py                      IoContext.open_metadata(); result sinks on compute result (Task 6)
    results.py                        RESULT_SINKS: fingerprint -> file rows, sha1 pairs, dirty marks (Task 8)
    compute/handlers.py               + fingerprint (sha1 only in M2) (Task 7)
    io/scan.py                        scan job (Task 9)
    io/regroup.py                     regroup job incl. auto-execution of identical groups (Task 10)
    io/actions.py                     apply_action, purge_trash, clear_empty_dirs (Task 11)
    io/archive_move.py                archive_move job (Task 12)
    io/import_v1.py                   import_v1 job (Task 13)
    io/__init__.py                    IO_HANDLERS += scan, regroup, apply_action, purge_trash, archive_move, import_v1
    io/planners.py                    plan_scans, plan_purge (Task 14)
    types.py                          KIND_OF += the above (Task 7)
  api/
    folders.py                        folders, ignored directories, extensions, scan trigger (Task 15)
    groups.py                         review queue, group detail, resolve, dismiss, pair judgment (Task 16)
    files.py                          file detail, original content streaming (Task 16)
    trash.py                          trash listing, restore (Task 17)
    operations.py                     archive-move, import-v1 (Task 17)
    overview.py                       + per-folder counts, open groups (Task 18)
web/src/
  pages/Folders.tsx                   (Task 19)
  pages/Groups.tsx                    (Task 20)
  pages/GroupDetail.tsx               (Task 21)
  pages/Jobs.tsx                      jobs, settings, trash, archive move, import (Task 22)
  components/Layout.tsx               nav links (Task 19)
  types.ts                            + Folder, Extension, Group, GroupMember, Pair, FileInfo, Action
tests/unit/                           test_domain_*.py, test_db_*.py
tests/integration/                    test_scan_job.py, test_pipeline_sha1.py, test_actions.py,
                                      test_archive_move.py, test_import_v1.py, test_api_*.py
tests/fixtures/v1_schema.sql          v1 CREATE TABLE statements for the import tests (Task 13)
docs/decisions/0004-m2-implementation-choices.md (Task 1)
```

---

### Task 1: Domain schema (migration 0002) and folder / extension / file queries

**Files:**
- Create: `visualstore/db/migrations/profile/0002_domain.sql`, `visualstore/db/folders.py`, `visualstore/db/files.py`, `docs/decisions/0004-m2-implementation-choices.md`, `tests/unit/test_db_folders.py`, `tests/unit/test_db_files.py`
- Modify: `tests/unit/test_migrate.py` (expected tables)

**Interfaces:**
- Consumes: `connect`, `migrate`, `migrations_dir("profile")`, `new_id`, `now_iso`.
- Produces, in `visualstore.db.folders` (all take `conn` first; ids are `bytes`):
  - `FolderError(Exception)`; `create_folder(conn, *, name, path, role, compare_scope, priority=0) -> bytes` (validates: absolute existing dir, no nesting with existing folders, unique name); `update_folder(conn, folder_id, **fields) -> None` (name, role, compare_scope, enabled, priority; path is immutable); `delete_folder(conn, folder_id) -> None`; `get_folder(conn, folder_id) -> Row | None`; `list_folders(conn) -> list[Row]` ordered by priority, name; `folder_to_dict(row) -> dict`.
  - `add_ignored(conn, folder_id, directory, recursive=True) -> bytes`; `remove_ignored(conn, ignored_id)`; `list_ignored(conn, folder_id) -> list[Row]`.
  - `list_extensions(conn) -> list[Row]`; `upsert_extension(conn, extension, media_type, enabled=True) -> bytes`; `set_extension_enabled(conn, extension_id, enabled)`; `extension_map(conn) -> dict[str, Row]` lowercase extension → row (enabled only).
- Produces, in `visualstore.db.files`:
  - `insert_file(conn, *, folder_id, directory, name, extension_id, size, mtime, state="new", sha1=None, file_id=None) -> bytes`; `get_file(conn, file_id) -> Row | None`; `find_file(conn, folder_id, directory, name) -> Row | None`; `list_files_in_folder(conn, folder_id) -> list[Row]`; `update_path(conn, file_id, directory, name)`; `reset_for_recompute(conn, file_id)` (state new, sha1 NULL, deletes its vector pairs — M3 — and enqueues dirty mark; in M2 deletes `source='vector'` pairs which do not exist yet, harmless); `set_missing(conn, file_ids)`; `delete_files(conn, file_ids)` (cascades); `set_fingerprint_result(conn, file_id, *, sha1, state)`; `files_by_sha1(conn, sha1) -> list[Row]`; `count_by_folder_and_state(conn) -> dict[str, dict[str, int]]` keyed by folder id string; `file_to_dict(row, folder_path) -> dict` with `relative_path` and `absolute_path`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_db_folders.py`:

```python
import pytest

from visualstore.db import folders
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "media"
    for sub in ("images", "images/sub", "photos", "inbox"):
        (root / sub).mkdir(parents=True)
    return root


def test_create_and_list_folder(conn, media):
    fid = folders.create_folder(
        conn, name="Images", path=str(media / "images"), role="archive", compare_scope="other_directories"
    )
    [row] = folders.list_folders(conn)
    assert row["id"] == fid and row["role"] == "archive" and row["enabled"] == 1
    d = folders.folder_to_dict(row)
    assert d["path"] == str(media / "images") and d["compare_scope"] == "other_directories"


@pytest.mark.parametrize("bad_role,bad_scope", [("sealed", "all"), ("archive", "everything")])
def test_invalid_enums_are_rejected(conn, media, bad_role, bad_scope):
    with pytest.raises(folders.FolderError):
        folders.create_folder(conn, name="x", path=str(media / "images"), role=bad_role, compare_scope=bad_scope)


def test_path_must_be_an_existing_absolute_directory(conn, media):
    with pytest.raises(folders.FolderError, match="absolute"):
        folders.create_folder(conn, name="rel", path="images", role="inbox", compare_scope="all")
    with pytest.raises(folders.FolderError, match="exist"):
        folders.create_folder(conn, name="ghost", path=str(media / "nope"), role="inbox", compare_scope="all")


def test_folders_must_not_nest(conn, media):
    """Review Focus 1: parent, child, equal, and trailing-slash variants are all rejected."""
    folders.create_folder(conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all")
    for path, label in [
        (str(media / "images" / "sub"), "child"),
        (str(media), "parent"),
        (str(media / "images"), "same"),
        (str(media / "images") + "/", "trailing slash"),
    ]:
        with pytest.raises(folders.FolderError, match="nest|already"):
            folders.create_folder(conn, name=label, path=path, role="inbox", compare_scope="all")
    folders.create_folder(conn, name="Photos", path=str(media / "photos"), role="archive", compare_scope="all")
    assert len(folders.list_folders(conn)) == 2


def test_name_is_unique_case_insensitively(conn, media):
    folders.create_folder(conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all")
    with pytest.raises(folders.FolderError, match="name"):
        folders.create_folder(conn, name="IMAGES", path=str(media / "photos"), role="archive", compare_scope="all")


def test_update_and_delete(conn, media):
    fid = folders.create_folder(conn, name="Inbox", path=str(media / "inbox"), role="inbox", compare_scope="all")
    folders.update_folder(conn, fid, role="archive", priority=5, enabled=False, name="Old inbox")
    row = folders.get_folder(conn, fid)
    assert (row["role"], row["priority"], row["enabled"], row["name"]) == ("archive", 5, 0, "Old inbox")
    with pytest.raises(folders.FolderError):
        folders.update_folder(conn, fid, path="/elsewhere")
    folders.delete_folder(conn, fid)
    assert folders.get_folder(conn, fid) is None


def test_ignored_directories(conn, media):
    fid = folders.create_folder(conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all")
    iid = folders.add_ignored(conn, fid, "Thumbs/", recursive=False)
    [row] = folders.list_ignored(conn, fid)
    assert row["directory"] == "Thumbs" and row["recursive"] == 0  # normalised, no slashes
    with pytest.raises(folders.FolderError):
        folders.add_ignored(conn, fid, "thumbs")  # NOCASE duplicate
    folders.remove_ignored(conn, iid)
    assert folders.list_ignored(conn, fid) == []
    folders.add_ignored(conn, fid, "a/b")
    folders.delete_folder(conn, fid)
    assert conn.execute("SELECT COUNT(*) FROM ignored_directory").fetchone()[0] == 0  # cascade


def test_default_extensions_are_seeded_and_editable(conn):
    ext = folders.extension_map(conn)
    assert ext["jpg"]["media_type"] == "image" and ext["gif"]["media_type"] == "animated"
    assert ext["mp4"]["media_type"] == "video" and "zip" not in ext
    zid = folders.upsert_extension(conn, "ZIP", "other")
    assert folders.extension_map(conn)["zip"]["id"] == zid
    folders.set_extension_enabled(conn, zid, False)
    assert "zip" not in folders.extension_map(conn)
    assert any(r["extension"] == "zip" and r["enabled"] == 0 for r in folders.list_extensions(conn))
    with pytest.raises(folders.FolderError):
        folders.upsert_extension(conn, "psd", "layered")
```

`tests/unit/test_db_files.py`:

```python
import pytest

from visualstore.db import files, folders
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


@pytest.fixture
def folder(conn, tmp_path):
    (tmp_path / "lib").mkdir()
    return folders.create_folder(conn, name="Lib", path=str(tmp_path / "lib"), role="inbox", compare_scope="all")


def test_insert_find_and_unique_nocase(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(conn, folder_id=folder, directory="A/B", name="Pic.JPG", extension_id=ext, size=10, mtime=5)
    assert files.find_file(conn, folder, "a/b", "pic.jpg")["id"] == fid
    with pytest.raises(Exception):  # sqlite IntegrityError on the NOCASE unique index
        files.insert_file(conn, folder_id=folder, directory="a/B", name="PIC.jpg", extension_id=ext, size=1, mtime=1)
    d = files.file_to_dict(files.get_file(conn, fid), folder_path="/media/lib")
    assert d["relative_path"] == "A/B/Pic.JPG" and d["absolute_path"] == "/media/lib/A/B/Pic.JPG"
    root = files.insert_file(conn, folder_id=folder, directory="", name="r.jpg", extension_id=ext, size=1, mtime=1)
    assert files.file_to_dict(files.get_file(conn, root), "/m")["relative_path"] == "r.jpg"


def test_state_transitions_and_sha1_bucket(conn, folder):
    ext = folders.extension_map(conn)["png"]["id"]
    a = files.insert_file(conn, folder_id=folder, directory="", name="a.png", extension_id=ext, size=3, mtime=1)
    b = files.insert_file(conn, folder_id=folder, directory="", name="b.png", extension_id=ext, size=3, mtime=1)
    files.set_fingerprint_result(conn, a, sha1=b"\x01" * 20, state="fingerprinted")
    files.set_fingerprint_result(conn, b, sha1=b"\x01" * 20, state="fingerprinted")
    assert {r["id"] for r in files.files_by_sha1(conn, b"\x01" * 20)} == {a, b}
    files.reset_for_recompute(conn, a)
    row = files.get_file(conn, a)
    assert row["state"] == "new" and row["sha1"] is None
    files.set_missing(conn, [b])
    assert files.get_file(conn, b)["state"] == "missing"
    files.update_path(conn, b, "moved", "b2.png")
    assert files.find_file(conn, folder, "moved", "b2.png")["id"] == b
    files.delete_files(conn, [a, b])
    assert files.list_files_in_folder(conn, folder) == []


def test_count_by_folder_and_state(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    files.insert_file(conn, folder_id=folder, directory="", name="1.jpg", extension_id=ext, size=1, mtime=1)
    f2 = files.insert_file(conn, folder_id=folder, directory="", name="2.jpg", extension_id=ext, size=1, mtime=1)
    files.set_fingerprint_result(conn, f2, sha1=b"\x02" * 20, state="fingerprinted")
    from visualstore.ids import id_to_str

    counts = files.count_by_folder_and_state(conn)
    assert counts[id_to_str(folder)] == {"new": 1, "fingerprinted": 1}


def test_deleting_a_folder_cascades_to_files(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    files.insert_file(conn, folder_id=folder, directory="", name="1.jpg", extension_id=ext, size=1, mtime=1)
    folders.delete_folder(conn, folder)
    assert conn.execute("SELECT COUNT(*) FROM file").fetchone()[0] == 0
```

Update `tests/unit/test_migrate.py`: the profile parametrisation becomes `("profile", {"job", "setting", "folder", "ignored_directory", "extension", "file", "pair", "file_group", "file_group_member", "regroup_queue", "action"})` and the expected version for `profile` becomes 2 — change the assertion to `assert version == {"global": 1, "profile": 2, "vectors": 1}[name]` and in `test_migrate_is_idempotent` expect 2.

Run: `uv run pytest tests/unit/test_db_folders.py tests/unit/test_db_files.py tests/unit/test_migrate.py -q`
Expected: FAIL — `ModuleNotFoundError` for `visualstore.db.folders`, and the migration test expects tables that do not exist.

- [ ] **Step 2: Write the migration**

`visualstore/db/migrations/profile/0002_domain.sql`:

```sql
-- Milestone 2: libraries, files, byte-identical pairs, groups, actions (design §5.1).

CREATE TABLE folder (
    id            BLOB PRIMARY KEY,
    name          TEXT NOT NULL COLLATE NOCASE UNIQUE,
    path          TEXT NOT NULL,                    -- absolute container path, no trailing slash
    role          TEXT NOT NULL CHECK (role IN ('archive', 'inbox')),
    compare_scope TEXT NOT NULL CHECK (compare_scope IN ('all', 'other_directories', 'other_folders')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    priority      INTEGER NOT NULL DEFAULT 0,       -- lower wins when several archives hold copies
    created_at    TEXT NOT NULL
);

CREATE TABLE ignored_directory (
    id        BLOB PRIMARY KEY,
    folder_id BLOB NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
    directory TEXT NOT NULL COLLATE NOCASE,          -- relative, '/'-separated, no edge slashes
    recursive INTEGER NOT NULL DEFAULT 1,
    UNIQUE (folder_id, directory)
);

CREATE TABLE extension (
    id         BLOB PRIMARY KEY,
    extension  TEXT NOT NULL COLLATE NOCASE UNIQUE,  -- lowercase, no dot
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'animated', 'video', 'other')),
    enabled    INTEGER NOT NULL DEFAULT 1
);

INSERT INTO extension (id, extension, media_type) VALUES
    (randomblob(16), 'jpg', 'image'), (randomblob(16), 'jpeg', 'image'), (randomblob(16), 'png', 'image'),
    (randomblob(16), 'webp', 'image'), (randomblob(16), 'avif', 'image'), (randomblob(16), 'heic', 'image'),
    (randomblob(16), 'heif', 'image'), (randomblob(16), 'bmp', 'image'), (randomblob(16), 'tiff', 'image'),
    (randomblob(16), 'tif', 'image'),
    (randomblob(16), 'gif', 'animated'), (randomblob(16), 'apng', 'animated'),
    (randomblob(16), 'mp4', 'video'), (randomblob(16), 'mkv', 'video'), (randomblob(16), 'webm', 'video'),
    (randomblob(16), 'mov', 'video'), (randomblob(16), 'avi', 'video');

CREATE TABLE file (
    id           BLOB PRIMARY KEY,
    folder_id    BLOB NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
    directory    TEXT NOT NULL COLLATE NOCASE,       -- '' for the folder root
    name         TEXT NOT NULL COLLATE NOCASE,       -- includes the extension
    extension_id BLOB NOT NULL REFERENCES extension(id),
    size         INTEGER NOT NULL,
    mtime        INTEGER NOT NULL,                   -- st_mtime_ns
    sha1         BLOB,
    state        TEXT NOT NULL CHECK (state IN ('new', 'fingerprinted', 'unreadable', 'not_media', 'missing')),
    created_at   TEXT NOT NULL,
    UNIQUE (folder_id, directory, name)
);
CREATE INDEX file_sha1 ON file (sha1) WHERE sha1 IS NOT NULL;
CREATE INDEX file_state ON file (state);
CREATE INDEX file_folder_dir ON file (folder_id, directory);

CREATE TABLE pair (
    file_a          BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    file_b          BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    distance        REAL,
    source          TEXT NOT NULL CHECK (source IN ('sha1', 'vector', 'import')),
    judgment        TEXT NOT NULL DEFAULT 'none' CHECK (judgment IN ('none', 'related_keep', 'unrelated')),
    judged_at       TEXT,
    judgment_origin TEXT CHECK (judgment_origin IN ('user', 'import_v1', 'rule')),
    model_id        TEXT,
    PRIMARY KEY (file_a, file_b),
    CHECK (file_a < file_b)
);
CREATE INDEX pair_file_b ON pair (file_b);

CREATE TABLE file_group (
    id            BLOB PRIMARY KEY,
    member_count  INTEGER NOT NULL,
    min_distance  REAL,
    has_identical INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL CHECK (status IN ('open', 'auto_resolved', 'resolved', 'dismissed')),
    updated_at    TEXT NOT NULL
);
CREATE INDEX file_group_status ON file_group (status, updated_at);

CREATE TABLE file_group_member (
    group_id BLOB NOT NULL REFERENCES file_group(id) ON DELETE CASCADE,
    file_id  BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    decision TEXT NOT NULL DEFAULT 'undecided' CHECK (decision IN ('keep', 'delete', 'undecided')),
    PRIMARY KEY (group_id, file_id)
);
CREATE UNIQUE INDEX file_group_member_file ON file_group_member (file_id);  -- one group per file

-- Files whose connected component must be recomputed by the regroup job.
CREATE TABLE regroup_queue (
    file_id BLOB PRIMARY KEY REFERENCES file(id) ON DELETE CASCADE
);

-- Audit and undo. file_id has no foreign key: a trashed file's row is gone, the action remains.
CREATE TABLE action (
    id            BLOB PRIMARY KEY,
    type          TEXT NOT NULL CHECK (type IN ('trash', 'restore', 'move', 'rename', 'purge')),
    file_id       BLOB,
    before        TEXT NOT NULL,   -- JSON
    after         TEXT NOT NULL,   -- JSON
    at            TEXT NOT NULL,
    superseded_at TEXT             -- set on a trash action once restored or purged
);
CREATE INDEX action_type_at ON action (type, at);
```

- [ ] **Step 3: Implement folder, ignored-directory and extension queries**

`visualstore/db/folders.py`:

```python
"""Folder, IgnoredDirectory and Extension queries (design §5.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from visualstore.clock import now_iso
from visualstore.domain.paths import normalise_relative
from visualstore.ids import id_to_str, new_id

ROLES = ("archive", "inbox")
SCOPES = ("all", "other_directories", "other_folders")
MEDIA_TYPES = ("image", "animated", "video", "other")


class FolderError(Exception):
    pass


# -- folders -----------------------------------------------------------------------------


def _check_enums(role: str | None, scope: str | None) -> None:
    if role is not None and role not in ROLES:
        raise FolderError(f"role must be one of {ROLES}")
    if scope is not None and scope not in SCOPES:
        raise FolderError(f"compare_scope must be one of {SCOPES}")


def _normalise_path(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        raise FolderError("Folder path must be an absolute path inside the container.")
    if not p.is_dir():
        raise FolderError(f"Folder path does not exist or is not a directory: {path}")
    return str(p.resolve()).rstrip("/") or "/"


def _check_no_nesting(conn: sqlite3.Connection, path: str) -> None:
    candidate = Path(path)
    for row in conn.execute("SELECT name, path FROM folder"):
        existing = Path(row["path"])
        if candidate == existing:
            raise FolderError(f"A Folder already uses this path: {row['name']}")
        if candidate.is_relative_to(existing) or existing.is_relative_to(candidate):
            raise FolderError(
                f"Folders must not nest: {path} overlaps Folder '{row['name']}' at {row['path']}. "
                "Use an ignored directory instead."
            )


def create_folder(
    conn: sqlite3.Connection, *, name: str, path: str, role: str, compare_scope: str, priority: int = 0
) -> bytes:
    _check_enums(role, compare_scope)
    if not name.strip():
        raise FolderError("Folder name is required.")
    normalised = _normalise_path(path)
    _check_no_nesting(conn, normalised)
    if conn.execute("SELECT 1 FROM folder WHERE name = ?", (name,)).fetchone():
        raise FolderError(f"A Folder named '{name}' already exists (names are case-insensitive).")
    folder_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO folder (id, name, path, role, compare_scope, priority, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (folder_id, name.strip(), normalised, role, compare_scope, priority, now_iso()),
        )
    return folder_id


_UPDATABLE = {"name", "role", "compare_scope", "enabled", "priority"}


def update_folder(conn: sqlite3.Connection, folder_id: bytes, **fields) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise FolderError(f"These Folder fields cannot be changed: {sorted(unknown)}")
    _check_enums(fields.get("role"), fields.get("compare_scope"))
    if "name" in fields:
        clash = conn.execute(
            "SELECT 1 FROM folder WHERE name = ? AND id != ?", (fields["name"], folder_id)
        ).fetchone()
        if clash or not str(fields["name"]).strip():
            raise FolderError("Folder name must be unique and non-empty.")
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE folder SET {assignments} WHERE id = ?", (*fields.values(), folder_id))


def delete_folder(conn: sqlite3.Connection, folder_id: bytes) -> None:
    with conn:
        conn.execute("DELETE FROM folder WHERE id = ?", (folder_id,))


def get_folder(conn: sqlite3.Connection, folder_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM folder WHERE id = ?", (folder_id,)).fetchone()


def list_folders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM folder ORDER BY priority, name").fetchall()


def folder_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["enabled"] = bool(row["enabled"])
    return d


# -- ignored directories -----------------------------------------------------------------


def add_ignored(conn: sqlite3.Connection, folder_id: bytes, directory: str, recursive: bool = True) -> bytes:
    rel = normalise_relative(directory)
    if not rel:
        raise FolderError("Ignoring the Folder root would ignore everything; disable the Folder instead.")
    if conn.execute(
        "SELECT 1 FROM ignored_directory WHERE folder_id = ? AND directory = ?", (folder_id, rel)
    ).fetchone():
        raise FolderError(f"'{rel}' is already ignored.")
    ignored_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO ignored_directory (id, folder_id, directory, recursive) VALUES (?, ?, ?, ?)",
            (ignored_id, folder_id, rel, 1 if recursive else 0),
        )
    return ignored_id


def remove_ignored(conn: sqlite3.Connection, ignored_id: bytes) -> None:
    with conn:
        conn.execute("DELETE FROM ignored_directory WHERE id = ?", (ignored_id,))


def list_ignored(conn: sqlite3.Connection, folder_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ignored_directory WHERE folder_id = ? ORDER BY directory", (folder_id,)
    ).fetchall()


# -- extensions --------------------------------------------------------------------------


def list_extensions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM extension ORDER BY media_type, extension").fetchall()


def extension_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        r["extension"].lower(): r
        for r in conn.execute("SELECT * FROM extension WHERE enabled = 1")
    }


def upsert_extension(conn: sqlite3.Connection, extension: str, media_type: str, enabled: bool = True) -> bytes:
    if media_type not in MEDIA_TYPES:
        raise FolderError(f"media_type must be one of {MEDIA_TYPES}")
    ext = extension.strip().lstrip(".").lower()
    if not ext or "/" in ext or "." in ext:
        raise FolderError("Extension must be letters/digits without a dot.")
    row = conn.execute("SELECT id FROM extension WHERE extension = ?", (ext,)).fetchone()
    with conn:
        if row is None:
            ext_id = new_id()
            conn.execute(
                "INSERT INTO extension (id, extension, media_type, enabled) VALUES (?, ?, ?, ?)",
                (ext_id, ext, media_type, 1 if enabled else 0),
            )
            return ext_id
        conn.execute(
            "UPDATE extension SET media_type = ?, enabled = ? WHERE id = ?",
            (media_type, 1 if enabled else 0, row["id"]),
        )
        return bytes(row["id"])


def set_extension_enabled(conn: sqlite3.Connection, extension_id: bytes, enabled: bool) -> None:
    with conn:
        conn.execute("UPDATE extension SET enabled = ? WHERE id = ?", (1 if enabled else 0, extension_id))
```

This module imports `visualstore.domain.paths.normalise_relative`, which Task 2 implements fully; for Task 1 create `visualstore/domain/__init__.py` (empty) and a first `visualstore/domain/paths.py` containing only:

```python
"""Relative-path rules shared by scan, import and the API (design §5.1 File, §5.3)."""


def normalise_relative(text: str) -> str:
    """'/'-separated, no leading/trailing separators, '' for the root; backslashes become '/'."""
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("relative paths may not contain '..'")
    return "/".join(parts)
```

- [ ] **Step 4: Implement file queries**

`visualstore/db/files.py`:

```python
"""File table queries (design §5.1 File)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

STATES = ("new", "fingerprinted", "unreadable", "not_media", "missing")


def insert_file(
    conn: sqlite3.Connection,
    *,
    folder_id: bytes,
    directory: str,
    name: str,
    extension_id: bytes,
    size: int,
    mtime: int,
    state: str = "new",
    sha1: bytes | None = None,
    file_id: bytes | None = None,
) -> bytes:
    file_id = file_id or new_id()
    with conn:
        conn.execute(
            "INSERT INTO file (id, folder_id, directory, name, extension_id, size, mtime, sha1,"
            " state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, folder_id, directory, name, extension_id, size, mtime, sha1, state, now_iso()),
        )
    return file_id


def get_file(conn: sqlite3.Connection, file_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM file WHERE id = ?", (file_id,)).fetchone()


def find_file(conn: sqlite3.Connection, folder_id: bytes, directory: str, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM file WHERE folder_id = ? AND directory = ? AND name = ?",
        (folder_id, directory, name),
    ).fetchone()


def list_files_in_folder(conn: sqlite3.Connection, folder_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM file WHERE folder_id = ? ORDER BY directory, name", (folder_id,)
    ).fetchall()


def update_path(conn: sqlite3.Connection, file_id: bytes, directory: str, name: str) -> None:
    with conn:
        conn.execute("UPDATE file SET directory = ?, name = ? WHERE id = ?", (directory, name, file_id))


def update_stat(conn: sqlite3.Connection, file_id: bytes, size: int, mtime: int) -> None:
    with conn:
        conn.execute("UPDATE file SET size = ?, mtime = ? WHERE id = ?", (size, mtime, file_id))


def reset_for_recompute(conn: sqlite3.Connection, file_id: bytes) -> None:
    """Content changed: forget the hash and the similarity pairs; keep the id and judgments."""
    with conn:
        conn.execute("UPDATE file SET state = 'new', sha1 = NULL WHERE id = ?", (file_id,))
        conn.execute(
            "DELETE FROM pair WHERE source IN ('sha1', 'vector') AND (file_a = ? OR file_b = ?)",
            (file_id, file_id),
        )
        conn.execute("INSERT OR IGNORE INTO regroup_queue (file_id) VALUES (?)", (file_id,))


def set_missing(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    with conn:
        conn.executemany("UPDATE file SET state = 'missing' WHERE id = ?", [(f,) for f in file_ids])


def delete_files(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    ids = list(file_ids)
    with conn:
        # group members of the deleted files' groups must be regrouped
        conn.executemany(
            "INSERT OR IGNORE INTO regroup_queue (file_id)"
            " SELECT m2.file_id FROM file_group_member m1 JOIN file_group_member m2"
            " ON m1.group_id = m2.group_id WHERE m1.file_id = ? AND m2.file_id != ?",
            [(f, f) for f in ids],
        )
        conn.executemany("DELETE FROM file WHERE id = ?", [(f,) for f in ids])


def set_fingerprint_result(conn: sqlite3.Connection, file_id: bytes, *, sha1: bytes | None, state: str) -> None:
    with conn:
        conn.execute("UPDATE file SET sha1 = ?, state = ? WHERE id = ?", (sha1, state, file_id))


def files_by_sha1(conn: sqlite3.Connection, sha1: bytes) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM file WHERE sha1 = ? ORDER BY created_at, rowid", (sha1,)).fetchall()


def files_by_state(conn: sqlite3.Connection, state: str, folder_id: bytes | None = None, limit: int = 1000) -> list[sqlite3.Row]:
    if folder_id is None:
        return conn.execute(
            "SELECT * FROM file WHERE state = ? ORDER BY folder_id, directory, name LIMIT ?", (state, limit)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM file WHERE state = ? AND folder_id = ? ORDER BY directory, name LIMIT ?",
        (state, folder_id, limit),
    ).fetchall()


def count_by_folder_and_state(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute("SELECT folder_id, state, COUNT(*) AS n FROM file GROUP BY folder_id, state"):
        out.setdefault(id_to_str(row["folder_id"]), {})[row["state"]] = row["n"]
    return out


def relative_path(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def file_to_dict(row: sqlite3.Row, folder_path: str) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["folder_id"] = id_to_str(row["folder_id"])
    d["extension_id"] = id_to_str(row["extension_id"])
    d["sha1"] = row["sha1"].hex() if row["sha1"] else None
    d["relative_path"] = relative_path(row["directory"], row["name"])
    d["absolute_path"] = f"{folder_path.rstrip('/')}/{d['relative_path']}"
    return d
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_db_folders.py tests/unit/test_db_files.py tests/unit/test_migrate.py -q`
Expected: all passed (9 folder tests incl. 2 parametrised, 4 file tests, migrate tests with the new table set).

- [ ] **Step 6: Write ADR-0004 and commit**

`docs/decisions/0004-m2-implementation-choices.md`:

```markdown
# ADR-0004 · Milestone 2 implementation choices

Date: <today>
Status: accepted

## Context

Milestone 2 adds the domain tables and the byte-identical pipeline. A few details were left open by the design or are consequences of milestone 1's threading rule.

## Decisions

- **Archive priority is a column** (`folder.priority`, lower wins) rather than a Setting holding an ordered list. Same semantics as v1's OrderedFolders, simpler to edit per Folder.
- **`file.name` includes the extension**; v1 split it off. The extension row is still referenced for the media type. Import joins v1's `FileName + '.' + Extension`.
- **Default extensions are seeded by the migration** with `randomblob(16)` ids. v1 import matches extensions by name and only inserts missing ones (keeping the v1 id for those).
- **Result sinks**: compute results are persisted by `jobs/results.py` on the event-loop thread (scheduler), keeping the "only the loop touches registry connections" rule from ADR-0003. Io jobs open their own connection via `IoContext.open_metadata()`; `connect()` sets `timeout=30` because two connections now share the WAL.
- **Dirty tracking** for grouping is a table (`regroup_queue`) of file ids whose component must be recomputed, filled by result sinks, judgments, deletions and resets. `regroup` drains it.
- **A file is in at most one group** (unique index on `file_group_member.file_id`), which is what a connected component implies; the index makes the invariant enforceable.
- **Trash keeps the relative path** under `<folder>/.visualstore-trash/<yyyy-mm>/`; collisions get a numeric suffix; cross-device moves copy, verify size, then delete.
- **`action.file_id` has no foreign key**: a trashed file's row is deleted (its pairs and membership go with it) and the action row is the only memory of it; restore re-inserts a row with the recorded sha1 so no rehash is needed.
- **Restored files keep their old id** (recorded in `action.before`) so later milestones can correlate judgments.
- **Scan schedule** is a per-profile setting `scan.interval_hours` (default 24, 0 disables) handled by the existing planner mechanism.
- **SimilarFile import stays off**; the switch is milestone 3+ work ([11 §15]).

## Consequences

- Milestone 3's fingerprint handler extends the same `fingerprint` job type (sha1 → sha1 + vectors + thumbnail); its result sink grows accordingly.
- The compare-scope filter is not needed for sha1 pairs (design §7.4: byte-identical pairs ignore scope) and is therefore not implemented in milestone 2.
```

```bash
git add visualstore/db/migrations/profile/0002_domain.sql visualstore/db/folders.py visualstore/db/files.py visualstore/domain docs/decisions/0004-m2-implementation-choices.md tests/unit/test_db_folders.py tests/unit/test_db_files.py tests/unit/test_migrate.py
git commit -m "feat: domain schema (folders, files, pairs, groups, actions) and folder/file queries

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Also add the row `| [0004](0004-m2-implementation-choices.md) | Milestone 2 implementation choices | accepted |` to `docs/decisions/README.md` in the same commit.

---

### Task 2: Pure scan logic — path rules, directory diff, move detection

**Files:**
- Create: `visualstore/domain/scan.py`, `tests/unit/test_domain_paths.py`, `tests/unit/test_domain_scan.py`
- Modify: `visualstore/domain/paths.py` (complete)

**Interfaces:**
- Produces, in `visualstore.domain.paths`: `normalise_relative(text) -> str` (Task 1); `split_relative(rel) -> tuple[str, str]` directory and name; `join_relative(directory, name) -> str`; `is_under(rel, ignored: str, recursive: bool) -> bool`; `path_key(text) -> str` ASCII-only case folding used wherever the code compares paths the way the NOCASE index does.
- Produces, in `visualstore.domain.scan` (pure, no I/O, no sqlite):
  - `DiskEntry(directory: str, name: str, extension: str, size: int, mtime: int)`; `DbFile(id: bytes, directory: str, name: str, size: int, mtime: int, sha1: bytes | None)`.
  - `ScanPlan(insert: list[DiskEntry], changed: list[tuple[bytes, DiskEntry]], unchanged: int, missing: list[DbFile])`.
  - `diff(disk: Iterable[DiskEntry], db: Iterable[DbFile]) -> ScanPlan` — matching by `path_key(directory)`, `path_key(name)`.
  - `MoveCandidate(missing: DbFile, inserted: DiskEntry)`; `move_candidates(plan) -> dict[int, tuple[list[DbFile], list[DiskEntry]]]` grouped by size for files whose size matches at least one missing file; `resolve_moves(missing_sha1: dict[bytes, bytes], inserted_sha1: dict[tuple[str, str], bytes]) -> list[tuple[bytes, tuple[str, str]]]` pairs each missing file id with exactly one new location when the sha1 matches exactly one new file (ambiguous or absent matches are not moves).
  - `should_skip_directory(rel_dir: str, ignored: list[tuple[str, bool]]) -> bool` — includes the fixed `.visualstore-trash` rule at the root.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_domain_paths.py`:

```python
import pytest

from visualstore.domain import paths


@pytest.mark.parametrize(
    "text,expected",
    [("", ""), ("/", ""), ("a/b/", "a/b"), ("/a//b", "a/b"), ("a\\b\\c", "a/b/c"), ("./a/./b", "a/b")],
)
def test_normalise_relative(text, expected):
    assert paths.normalise_relative(text) == expected


def test_parent_segments_are_rejected():
    with pytest.raises(ValueError):
        paths.normalise_relative("a/../b")


def test_split_and_join():
    assert paths.split_relative("a/b/c.jpg") == ("a/b", "c.jpg")
    assert paths.split_relative("c.jpg") == ("", "c.jpg")
    assert paths.join_relative("", "c.jpg") == "c.jpg"
    assert paths.join_relative("a/b", "c.jpg") == "a/b/c.jpg"


def test_is_under_respects_recursive_flag_and_case():
    assert paths.is_under("Thumbs/x", "thumbs", recursive=True)
    assert paths.is_under("thumbs", "Thumbs", recursive=False)
    assert not paths.is_under("thumbs/x", "thumbs", recursive=False)
    assert not paths.is_under("thumbsy", "thumbs", recursive=True)
    assert not paths.is_under("a/thumbs", "thumbs", recursive=True)  # anchored at the root


def test_path_key_folds_ascii_only():
    assert paths.path_key("A/B.JPG") == "a/b.jpg"
    assert paths.path_key("É") == "É"  # sqlite NOCASE folds ASCII only; so do we
```

`tests/unit/test_domain_scan.py`:

```python
from visualstore.domain.scan import (
    DbFile,
    DiskEntry,
    diff,
    move_candidates,
    resolve_moves,
    should_skip_directory,
)


def entry(path, size=1, mtime=1, ext="jpg"):
    directory, _, name = path.rpartition("/")
    return DiskEntry(directory, name, ext, size, mtime)


def dbfile(id_, path, size=1, mtime=1, sha1=None):
    directory, _, name = path.rpartition("/")
    return DbFile(id_, directory, name, size, mtime, sha1)


def test_diff_classifies_insert_changed_unchanged_missing():
    plan = diff(
        disk=[entry("a/1.jpg"), entry("a/2.jpg", size=9), entry("b/3.jpg")],
        db=[dbfile(b"1" * 16, "a/1.jpg"), dbfile(b"2" * 16, "a/2.jpg", size=5), dbfile(b"4" * 16, "c/4.jpg")],
    )
    assert plan.unchanged == 1
    assert [e.name for e in plan.insert] == ["3.jpg"]
    assert [(fid, e.size) for fid, e in plan.changed] == [(b"2" * 16, 9)]
    assert [f.name for f in plan.missing] == ["4.jpg"]


def test_diff_matches_paths_case_insensitively_like_the_index():
    """Review Focus 2 (part): a case-only rename on a case-insensitive share is not a new file."""
    plan = diff(disk=[entry("A/Pic.JPG")], db=[dbfile(b"1" * 16, "a/pic.jpg")])
    assert plan.unchanged == 1 and plan.insert == [] and plan.missing == []


def test_mtime_change_alone_marks_changed():
    plan = diff(disk=[entry("x.jpg", mtime=2)], db=[dbfile(b"1" * 16, "x.jpg", mtime=1)])
    assert len(plan.changed) == 1


def test_move_candidates_group_by_size_only_where_sizes_meet():
    plan = diff(
        disk=[entry("new/a.jpg", size=10), entry("new/b.jpg", size=20), entry("new/c.jpg", size=99)],
        db=[dbfile(b"a" * 16, "old/a.jpg", size=10), dbfile(b"b" * 16, "old/b.jpg", size=20), dbfile(b"z" * 16, "old/z.jpg", size=7)],
    )
    cands = move_candidates(plan)
    assert set(cands) == {10, 20}
    assert [f.id for f in cands[10][0]] == [b"a" * 16] and [e.name for e in cands[10][1]] == ["a.jpg"]


def test_resolve_moves_requires_exactly_one_match():
    """Review Focus 2: a unique sha1 match is a move; two identical new copies are not."""
    moves = resolve_moves(
        missing_sha1={b"a" * 16: b"\x0a" * 20, b"b" * 16: b"\x0b" * 20, b"c" * 16: b"\x0c" * 20},
        inserted_sha1={("new", "a.jpg"): b"\x0a" * 20, ("new", "b1.jpg"): b"\x0b" * 20, ("new", "b2.jpg"): b"\x0b" * 20},
    )
    assert moves == [(b"a" * 16, ("new", "a.jpg"))]


def test_skip_rules():
    ignored = [("Thumbs", True), ("raw", False)]
    assert should_skip_directory(".visualstore-trash", [])
    assert should_skip_directory(".visualstore-trash/2026-11/x", [])
    assert not should_skip_directory("sub/.visualstore-trash", [])  # only at the root
    assert should_skip_directory("thumbs/deep", ignored)
    assert should_skip_directory("raw", ignored)
    assert not should_skip_directory("raw/sub", ignored)
    assert not should_skip_directory("", ignored)
```

Run: `uv run pytest tests/unit/test_domain_paths.py tests/unit/test_domain_scan.py -q`
Expected: FAIL — `split_relative` etc. missing, `visualstore.domain.scan` missing.

- [ ] **Step 2: Implement**

`visualstore/domain/paths.py` (complete file):

```python
"""Relative-path rules shared by scan, import and the API (design §5.1 File, §5.3).

Paths in the database are '/'-separated and relative to the Folder; '' is the root. Comparisons
fold ASCII case only, exactly like SQLite's COLLATE NOCASE, so Python and the unique index agree.
"""

from __future__ import annotations

TRASH_DIR = ".visualstore-trash"


def normalise_relative(text: str) -> str:
    """'/'-separated, no leading/trailing separators, '' for the root; backslashes become '/'."""
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("relative paths may not contain '..'")
    return "/".join(parts)


def split_relative(rel: str) -> tuple[str, str]:
    directory, _, name = rel.rpartition("/")
    return directory, name


def join_relative(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def path_key(text: str) -> str:
    """ASCII-only case folding (what SQLite NOCASE does)."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def is_under(rel: str, ignored: str, recursive: bool) -> bool:
    a, b = path_key(rel), path_key(ignored)
    if a == b:
        return True
    return recursive and a.startswith(b + "/")
```

`visualstore/domain/scan.py`:

```python
"""Pure scan logic (design §7.1): compare a directory listing with the database and detect moves.

No I/O here. The scan job (jobs/io/scan.py) walks the disk, calls these, and writes the result.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from visualstore.domain.paths import TRASH_DIR, is_under, path_key


@dataclass(frozen=True)
class DiskEntry:
    directory: str
    name: str
    extension: str  # lowercase, no dot
    size: int
    mtime: int


@dataclass(frozen=True)
class DbFile:
    id: bytes
    directory: str
    name: str
    size: int
    mtime: int
    sha1: bytes | None


@dataclass
class ScanPlan:
    insert: list[DiskEntry] = field(default_factory=list)
    changed: list[tuple[bytes, DiskEntry]] = field(default_factory=list)
    unchanged: int = 0
    missing: list[DbFile] = field(default_factory=list)


def _key(directory: str, name: str) -> tuple[str, str]:
    return path_key(directory), path_key(name)


def diff(disk: Iterable[DiskEntry], db: Iterable[DbFile]) -> ScanPlan:
    plan = ScanPlan()
    by_key = {_key(f.directory, f.name): f for f in db}
    seen: set[tuple[str, str]] = set()
    for entry in disk:
        key = _key(entry.directory, entry.name)
        seen.add(key)
        existing = by_key.get(key)
        if existing is None:
            plan.insert.append(entry)
        elif existing.size == entry.size and existing.mtime == entry.mtime:
            plan.unchanged += 1
        else:
            plan.changed.append((existing.id, entry))
    plan.missing = [f for k, f in by_key.items() if k not in seen]
    return plan


def move_candidates(plan: ScanPlan) -> dict[int, tuple[list[DbFile], list[DiskEntry]]]:
    """Missing and inserted files bucketed by size, only for sizes present on both sides.
    Hashing is then limited to these files (design §7.1)."""
    missing_by_size: dict[int, list[DbFile]] = defaultdict(list)
    for f in plan.missing:
        missing_by_size[f.size].append(f)
    inserted_by_size: dict[int, list[DiskEntry]] = defaultdict(list)
    for e in plan.insert:
        inserted_by_size[e.size].append(e)
    return {
        size: (missing_by_size[size], inserted_by_size[size])
        for size in missing_by_size
        if size in inserted_by_size
    }


def resolve_moves(
    missing_sha1: dict[bytes, bytes], inserted_sha1: dict[tuple[str, str], bytes]
) -> list[tuple[bytes, tuple[str, str]]]:
    """A missing file moved to a new location when exactly one new file has the same sha1 and
    that new file matches exactly one missing file. Anything ambiguous is treated as delete + add."""
    new_by_sha: dict[bytes, list[tuple[str, str]]] = defaultdict(list)
    for loc, sha in inserted_sha1.items():
        new_by_sha[sha].append(loc)
    old_by_sha: dict[bytes, list[bytes]] = defaultdict(list)
    for fid, sha in missing_sha1.items():
        old_by_sha[sha].append(fid)
    moves = []
    for sha, old_ids in old_by_sha.items():
        new_locs = new_by_sha.get(sha, [])
        if len(old_ids) == 1 and len(new_locs) == 1:
            moves.append((old_ids[0], new_locs[0]))
    return moves


def should_skip_directory(rel_dir: str, ignored: list[tuple[str, bool]]) -> bool:
    if rel_dir and is_under(rel_dir, TRASH_DIR, recursive=True):
        return True
    return any(is_under(rel_dir, directory, recursive) for directory, recursive in ignored) if rel_dir else False
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_domain_paths.py tests/unit/test_domain_scan.py tests/unit/test_db_folders.py -q`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add visualstore/domain tests/unit/test_domain_paths.py tests/unit/test_domain_scan.py
git commit -m "feat: pure scan logic with case-folded diff and sha1 move detection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Grouping — connected components over pairs

**Files:**
- Create: `visualstore/domain/grouping.py`, `tests/unit/test_domain_grouping.py`

**Interfaces:**
- Produces: `PairEdge(a: bytes, b: bytes, distance: float | None, source: str, judgment: str)`; `components(files: Iterable[bytes], edges: Iterable[PairEdge], group_distance: float) -> list[Component]` where `Component(members: frozenset[bytes], min_distance: float | None, has_identical: bool)`; singletons (files with no surviving edge) are **not** returned. Edge rule (design §7.5): `judgment != "unrelated"` and (`source == "sha1"` or `distance is not None and distance <= group_distance`). `related_keep` edges count.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_domain_grouping.py`:

```python
from visualstore.domain.grouping import PairEdge, components

A, B, C, D, E = (bytes([i]) * 16 for i in range(1, 6))


def sha(a, b, judgment="none"):
    return PairEdge(min(a, b), max(a, b), 0.0, "sha1", judgment)


def vec(a, b, distance, judgment="none"):
    return PairEdge(min(a, b), max(a, b), distance, "vector", judgment)


def test_sha1_edges_form_a_component_and_singletons_are_dropped():
    comps = components([A, B, C, D], [sha(A, B), sha(B, C)], group_distance=0.2)
    assert len(comps) == 1
    [c] = comps
    assert c.members == {A, B, C} and c.has_identical and c.min_distance == 0.0


def test_unrelated_cuts_an_edge_related_keep_does_not():
    comps = components([A, B, C], [sha(A, B, "unrelated"), vec(B, C, 0.1, "related_keep")], 0.2)
    assert [c.members for c in comps] == [{B, C}]
    assert not comps[0].has_identical and comps[0].min_distance == 0.1


def test_vector_edges_beyond_group_distance_do_not_connect():
    comps = components([A, B, C], [vec(A, B, 0.3), vec(B, C, 0.15)], group_distance=0.2)
    assert [c.members for c in comps] == [{B, C}]


def test_null_distance_import_edges_never_connect():
    assert components([A, B], [PairEdge(A, B, None, "import", "none")], 0.5) == []


def test_two_components_sorted_deterministically():
    comps = components([A, B, C, D, E], [sha(D, E), sha(A, B)], 0.2)
    assert [sorted(c.members) for c in comps] == [[A, B], [D, E]]
```

Run: `uv run pytest tests/unit/test_domain_grouping.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement**

`visualstore/domain/grouping.py`:

```python
"""Groups are connected components of the pair graph (design §7.5). Pure union-find."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class PairEdge:
    a: bytes
    b: bytes
    distance: float | None
    source: str  # sha1 | vector | import
    judgment: str  # none | related_keep | unrelated


@dataclass(frozen=True)
class Component:
    members: frozenset[bytes]
    min_distance: float | None
    has_identical: bool


def is_edge(edge: PairEdge, group_distance: float) -> bool:
    if edge.judgment == "unrelated":
        return False
    if edge.source == "sha1":
        return True
    return edge.distance is not None and edge.distance <= group_distance


def components(files: Iterable[bytes], edges: Iterable[PairEdge], group_distance: float) -> list[Component]:
    parent: dict[bytes, bytes] = {f: f for f in files}

    def find(x: bytes) -> bytes:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: bytes, y: bytes) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    live = [e for e in edges if is_edge(e, group_distance)]
    for e in live:
        parent.setdefault(e.a, e.a)
        parent.setdefault(e.b, e.b)
        union(e.a, e.b)

    members: dict[bytes, set[bytes]] = {}
    for f in parent:
        members.setdefault(find(f), set()).add(f)
    stats: dict[bytes, tuple[float | None, bool]] = {}
    for e in live:
        root = find(e.a)
        min_d, ident = stats.get(root, (None, False))
        d = 0.0 if e.source == "sha1" else e.distance
        if d is not None and (min_d is None or d < min_d):
            min_d = d
        stats[root] = (min_d, ident or e.source == "sha1")

    out = [
        Component(frozenset(ms), *stats.get(root, (None, False)))
        for root, ms in members.items()
        if len(ms) > 1
    ]
    out.sort(key=lambda c: min(c.members))
    return out
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_domain_grouping.py -q` — Expected: 5 passed.

```bash
git add visualstore/domain/grouping.py tests/unit/test_domain_grouping.py
git commit -m "feat: union-find grouping over judged pairs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Default resolution rules

**Files:**
- Create: `visualstore/domain/resolution.py`, `tests/unit/test_domain_resolution.py`

**Interfaces:**
- Produces: `Member(file_id: bytes, folder_id: bytes, role: str, folder_priority: int, relative_path: str)`; `Preselection(decisions: dict[bytes, str], status: str, auto_executable: bool)` where decisions are `keep | delete | undecided`, status is `open | auto_resolved`; `preselect(members: Sequence[Member], *, all_identical: bool, auto_execute_identical: bool) -> Preselection` implementing design §8.2:
  - archive + inbox members: keep every archive member, delete every inbox member; if `all_identical` and the setting is on → `auto_resolved`, `auto_executable=True`; otherwise `open`.
  - only inbox: keep the first by `(relative_path sorted with path_key)`, delete the rest; always `open`; `auto_executable=True` only if `all_identical` and setting on (design: "标为 open，必须人看" — ruling: inbox-only identical groups are preselected but **never** auto-executed; see test).
  - only archive, several Folders: keep members of the Folder with the lowest `folder_priority`, delete the others; `open`; auto-executable if `all_identical` and setting on.
  - only archive, one Folder: all `undecided`, `open`, never auto-executable.
  - `all_identical=False` (any non-sha1 edge; milestone 3+): same preselection, never auto-executable.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_domain_resolution.py`:

```python
from visualstore.domain.resolution import Member, preselect

ARCH1, ARCH2, INBOX = b"\x01" * 16, b"\x02" * 16, b"\x03" * 16


def m(i, folder, role, prio=0, path="x.jpg"):
    return Member(bytes([i]) * 16, folder, role, prio, path)


def test_archive_plus_inbox_keeps_archive_and_auto_executes_identical():
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox"), m(3, INBOX, "inbox")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {members[0].file_id: "keep", members[1].file_id: "delete", members[2].file_id: "delete"}
    assert p.status == "auto_resolved" and p.auto_executable


def test_setting_off_disables_auto_execution_entirely():
    """Review Focus 4."""
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox")]
    p = preselect(members, all_identical=True, auto_execute_identical=False)
    assert p.decisions[members[1].file_id] == "delete"
    assert p.status == "open" and not p.auto_executable


def test_inbox_only_keeps_first_by_path_and_is_never_automatic():
    members = [m(1, INBOX, "inbox", path="b/2.jpg"), m(2, INBOX, "inbox", path="A/1.jpg")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {members[1].file_id: "keep", members[0].file_id: "delete"}
    assert p.status == "open" and not p.auto_executable


def test_archive_only_single_folder_is_undecided():
    """Review Focus 4: archive-only groups are never touched automatically."""
    members = [m(1, ARCH1, "archive"), m(2, ARCH1, "archive")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert set(p.decisions.values()) == {"undecided"}
    assert p.status == "open" and not p.auto_executable


def test_archive_only_several_folders_uses_priority():
    members = [m(1, ARCH1, "archive", prio=5), m(2, ARCH2, "archive", prio=1), m(3, ARCH1, "archive", prio=5)]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {members[1].file_id: "keep", members[0].file_id: "delete", members[2].file_id: "delete"}
    assert p.status == "auto_resolved" and p.auto_executable


def test_non_identical_groups_are_preselected_but_manual():
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox")]
    p = preselect(members, all_identical=False, auto_execute_identical=True)
    assert p.decisions[members[1].file_id] == "delete"
    assert p.status == "open" and not p.auto_executable
```

Run: `uv run pytest tests/unit/test_domain_resolution.py -q` — Expected: FAIL, module missing.

- [ ] **Step 2: Implement**

`visualstore/domain/resolution.py`:

```python
"""Default resolution rules for a duplicate group (design §8.2). Pure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from visualstore.domain.paths import path_key


@dataclass(frozen=True)
class Member:
    file_id: bytes
    folder_id: bytes
    role: str  # archive | inbox
    folder_priority: int
    relative_path: str


@dataclass(frozen=True)
class Preselection:
    decisions: dict[bytes, str]
    status: str  # open | auto_resolved
    auto_executable: bool


def preselect(
    members: Sequence[Member], *, all_identical: bool, auto_execute_identical: bool
) -> Preselection:
    archive = [m for m in members if m.role == "archive"]
    inbox = [m for m in members if m.role != "archive"]
    decisions: dict[bytes, str] = {}
    automatic_allowed = all_identical and auto_execute_identical

    if archive and inbox:
        for m in archive:
            decisions[m.file_id] = "keep"
        for m in inbox:
            decisions[m.file_id] = "delete"
        auto = automatic_allowed
    elif inbox:  # inbox only: keep the first by path, a human must confirm
        ordered = sorted(inbox, key=lambda m: path_key(m.relative_path))
        decisions[ordered[0].file_id] = "keep"
        for m in ordered[1:]:
            decisions[m.file_id] = "delete"
        auto = False
    else:  # archive only
        folders = {m.folder_id for m in archive}
        if len(folders) == 1:
            for m in archive:
                decisions[m.file_id] = "undecided"
            auto = False
        else:
            best = min(archive, key=lambda m: (m.folder_priority, path_key(m.relative_path))).folder_id
            for m in archive:
                decisions[m.file_id] = "keep" if m.folder_id == best else "delete"
            auto = automatic_allowed

    status = "auto_resolved" if auto else "open"
    return Preselection(decisions=decisions, status=status, auto_executable=auto)
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_domain_resolution.py -q` — Expected: 6 passed.

```bash
git add visualstore/domain/resolution.py tests/unit/test_domain_resolution.py
git commit -m "feat: default resolution rules for duplicate groups

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Pair, group, dirty-queue and action queries

**Files:**
- Create: `visualstore/db/pairs.py`, `visualstore/db/groups.py`, `visualstore/db/actions.py`, `tests/unit/test_db_pairs_groups.py`

**Interfaces:**
- `visualstore.db.pairs`: `ordered(a, b) -> tuple[bytes, bytes]`; `upsert_sha1_pair(conn, a, b) -> None` (sets `source='sha1', distance=0`, keeps any judgment); `set_judgment(conn, a, b, judgment, origin) -> bool`; `edges_touching(conn, file_ids) -> list[Row]`; `pairs_of_file(conn, file_id) -> list[Row]`; `pair_to_dict(row) -> dict`.
- `visualstore.db.groups`: `mark_dirty(conn, file_ids)`; `pop_dirty(conn, limit=5000) -> list[bytes]`; `dirty_count(conn) -> int`; `group_of_file(conn, file_id) -> Row | None`; `get_group(conn, group_id) -> Row | None`; `members(conn, group_id) -> list[Row]` (member + file + folder columns: `file_id, decision, directory, name, size, sha1, state, folder_id, folder_name, folder_path, role, folder_priority`); `member_file_ids(conn, group_ids) -> list[bytes]`; `replace_groups(conn, remove_group_ids, new_groups: list[NewGroup]) -> list[bytes]` where `NewGroup(members: frozenset[bytes], min_distance, has_identical, status, decisions: dict[bytes, str])`, one transaction; `set_decisions(conn, group_id, decisions)`; `set_status(conn, group_id, status)`; `list_groups(conn, *, status=None, folder_id=None, has_identical=None, min_size=None, limit=50, offset=0) -> list[Row]`; `count_by_status(conn) -> dict[str, int]`; `group_to_dict(row) -> dict`.
- `visualstore.db.actions`: `record(conn, type, file_id, before: dict, after: dict) -> bytes`; `get(conn, action_id) -> Row | None`; `list_trash(conn, limit=500) -> list[Row]` (type trash, `superseded_at IS NULL`, newest first); `supersede(conn, action_id)`; `trash_older_than(conn, cutoff_iso) -> list[Row]`; `action_to_dict(row) -> dict` (before/after decoded).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_db_pairs_groups.py`:

```python
import pytest

from visualstore.db import actions, files, folders, groups, pairs
from visualstore.db.connection import connect
from visualstore.db.groups import NewGroup
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "p.db")
    migrate(conn, migrations_dir("profile"))
    (tmp_path / "arch").mkdir()
    (tmp_path / "inbox").mkdir()
    arch = folders.create_folder(conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all")
    inbox = folders.create_folder(conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    ids = [
        files.insert_file(conn, folder_id=f, directory=d, name=n, extension_id=ext, size=1, mtime=1)
        for f, d, n in [(arch, "", "a.jpg"), (inbox, "x", "b.jpg"), (inbox, "", "c.jpg"), (arch, "", "d.jpg")]
    ]
    return conn, arch, inbox, ids


def test_sha1_pair_upsert_keeps_judgment_and_orders_ids(db):
    conn, _, _, (a, b, c, _) = db
    pairs.upsert_sha1_pair(conn, b, a)
    [row] = pairs.pairs_of_file(conn, a)
    assert (row["file_a"], row["file_b"]) == pairs.ordered(a, b)
    assert row["source"] == "sha1" and row["distance"] == 0.0 and row["judgment"] == "none"
    assert pairs.set_judgment(conn, a, b, "unrelated", "user")
    pairs.upsert_sha1_pair(conn, a, b)  # re-fingerprinting must not erase a human judgment
    assert pairs.pairs_of_file(conn, a)[0]["judgment"] == "unrelated"
    assert not pairs.set_judgment(conn, a, c, "unrelated", "user")  # no such pair
    pairs.upsert_sha1_pair(conn, b, c)
    assert len(pairs.edges_touching(conn, [a])) == 1 and len(pairs.edges_touching(conn, [b])) == 2
    d = pairs.pair_to_dict(pairs.pairs_of_file(conn, a)[0])
    assert isinstance(d["file_a"], str) and d["judgment_origin"] == "user"


def test_dirty_queue(db):
    conn, _, _, (a, b, *_) = db
    groups.mark_dirty(conn, [a, b, a])
    assert groups.dirty_count(conn) == 2
    assert set(groups.pop_dirty(conn)) == {a, b}
    assert groups.dirty_count(conn) == 0
    files.delete_files(conn, [a])  # cascade removes nothing from an empty queue, no error


def test_replace_groups_and_queries(db):
    conn, arch, inbox, (a, b, c, d) = db
    g1 = groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {a: "keep", b: "delete"})]
    )[0]
    assert groups.group_of_file(conn, a)["id"] == g1
    ms = groups.members(conn, g1)
    assert {m["file_id"] for m in ms} == {a, b}
    by_id = {m["file_id"]: m for m in ms}
    assert by_id[a]["role"] == "archive" and by_id[b]["directory"] == "x" and by_id[a]["decision"] == "keep"
    assert groups.count_by_status(conn) == {"open": 1, "auto_resolved": 0, "resolved": 0, "dismissed": 0}

    # a file can be in one group only: re-grouping {a, b, c} replaces g1
    [g2] = groups.replace_groups(
        conn, [g1], [NewGroup(frozenset({a, b, c}), 0.0, True, "auto_resolved", {a: "keep", b: "delete", c: "delete"})]
    )
    assert groups.get_group(conn, g1) is None
    assert groups.get_group(conn, g2)["member_count"] == 3
    assert set(groups.member_file_ids(conn, [g2])) == {a, b, c}

    groups.set_decisions(conn, g2, {c: "keep"})
    assert {m["file_id"]: m["decision"] for m in groups.members(conn, g2)}[c] == "keep"
    groups.set_status(conn, g2, "resolved")
    assert groups.list_groups(conn, status="open") == []
    assert [groups.group_to_dict(r)["id"] for r in groups.list_groups(conn, status="resolved")]
    assert len(groups.list_groups(conn, folder_id=inbox)) == 1
    assert groups.list_groups(conn, folder_id=inbox, min_size=4) == []
    assert len(groups.list_groups(conn, has_identical=True)) == 1

    files.delete_files(conn, [b])  # member deletion cascades and marks the rest dirty
    assert groups.get_group(conn, g2)["member_count"] == 3  # stale until regroup recomputes
    assert set(groups.pop_dirty(conn)) == {a, c}


def test_actions_record_list_supersede(db):
    conn, _, _, (a, *_) = db
    act = actions.record(conn, "trash", a, {"path": "/m/a.jpg", "sha1": "00"}, {"path": "/m/.visualstore-trash/2026-11/a.jpg"})
    [row] = actions.list_trash(conn)
    assert row["id"] == act and actions.action_to_dict(row)["before"]["sha1"] == "00"
    assert actions.trash_older_than(conn, "2999-01-01T00:00:00+00:00") == [row]
    assert actions.trash_older_than(conn, "2000-01-01T00:00:00+00:00") == []
    actions.supersede(conn, act)
    assert actions.list_trash(conn) == []
    assert actions.get(conn, act)["superseded_at"] is not None
```

Run: `uv run pytest tests/unit/test_db_pairs_groups.py -q` — Expected: FAIL, modules missing.

- [ ] **Step 2: Implement**

`visualstore/db/pairs.py`:

```python
"""Pair table queries (design §5.1 Pair)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from visualstore.clock import now_iso
from visualstore.ids import id_to_str

JUDGMENTS = ("none", "related_keep", "unrelated")


def ordered(a: bytes, b: bytes) -> tuple[bytes, bytes]:
    return (a, b) if a < b else (b, a)


def upsert_sha1_pair(conn: sqlite3.Connection, a: bytes, b: bytes) -> None:
    fa, fb = ordered(a, b)
    with conn:
        conn.execute(
            "INSERT INTO pair (file_a, file_b, distance, source) VALUES (?, ?, 0.0, 'sha1')"
            " ON CONFLICT(file_a, file_b) DO UPDATE SET source = 'sha1', distance = 0.0",
            (fa, fb),
        )


def set_judgment(conn: sqlite3.Connection, a: bytes, b: bytes, judgment: str, origin: str) -> bool:
    if judgment not in JUDGMENTS:
        raise ValueError(f"judgment must be one of {JUDGMENTS}")
    fa, fb = ordered(a, b)
    with conn:
        cur = conn.execute(
            "UPDATE pair SET judgment = ?, judged_at = ?, judgment_origin = ?"
            " WHERE file_a = ? AND file_b = ?",
            (judgment, now_iso(), origin, fa, fb),
        )
    return cur.rowcount == 1


def edges_touching(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> list[sqlite3.Row]:
    ids = list(file_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM pair WHERE file_a IN ({marks}) OR file_b IN ({marks})", (*ids, *ids)
    ).fetchall()


def pairs_of_file(conn: sqlite3.Connection, file_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pair WHERE file_a = ? OR file_b = ? ORDER BY distance", (file_id, file_id)
    ).fetchall()


def pair_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["file_a"] = id_to_str(row["file_a"])
    d["file_b"] = id_to_str(row["file_b"])
    return d
```

`visualstore/db/groups.py`:

```python
"""Group, membership and dirty-queue queries (design §5.1 Group, §7.5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

STATUSES = ("open", "auto_resolved", "resolved", "dismissed")


@dataclass(frozen=True)
class NewGroup:
    members: frozenset[bytes]
    min_distance: float | None
    has_identical: bool
    status: str
    decisions: dict[bytes, str]


# -- dirty queue -------------------------------------------------------------------------


def mark_dirty(conn: sqlite3.Connection, file_ids: Iterable[bytes]) -> None:
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO regroup_queue (file_id) VALUES (?)", [(f,) for f in file_ids]
        )


def pop_dirty(conn: sqlite3.Connection, limit: int = 5000) -> list[bytes]:
    rows = conn.execute("SELECT file_id FROM regroup_queue LIMIT ?", (limit,)).fetchall()
    ids = [bytes(r["file_id"]) for r in rows]
    with conn:
        conn.executemany("DELETE FROM regroup_queue WHERE file_id = ?", [(f,) for f in ids])
    return ids


def dirty_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM regroup_queue").fetchone()[0]


# -- groups ------------------------------------------------------------------------------

_MEMBER_SQL = (
    "SELECT m.file_id, m.decision, f.directory, f.name, f.size, f.sha1, f.state,"
    " f.folder_id, fo.name AS folder_name, fo.path AS folder_path, fo.role, fo.priority AS folder_priority"
    " FROM file_group_member m JOIN file f ON f.id = m.file_id JOIN folder fo ON fo.id = f.folder_id"
)


def group_of_file(conn: sqlite3.Connection, file_id: bytes) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT g.* FROM file_group g JOIN file_group_member m ON m.group_id = g.id WHERE m.file_id = ?",
        (file_id,),
    ).fetchone()


def get_group(conn: sqlite3.Connection, group_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM file_group WHERE id = ?", (group_id,)).fetchone()


def members(conn: sqlite3.Connection, group_id: bytes) -> list[sqlite3.Row]:
    return conn.execute(
        f"{_MEMBER_SQL} WHERE m.group_id = ? ORDER BY fo.priority, fo.name, f.directory, f.name",
        (group_id,),
    ).fetchall()


def member_file_ids(conn: sqlite3.Connection, group_ids: Iterable[bytes]) -> list[bytes]:
    ids = list(group_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT file_id FROM file_group_member WHERE group_id IN ({marks})", ids
    ).fetchall()
    return [bytes(r["file_id"]) for r in rows]


def replace_groups(
    conn: sqlite3.Connection, remove_group_ids: Iterable[bytes], new_groups: list[NewGroup]
) -> list[bytes]:
    created: list[bytes] = []
    now = now_iso()
    with conn:
        conn.executemany("DELETE FROM file_group WHERE id = ?", [(g,) for g in remove_group_ids])
        for ng in new_groups:
            gid = new_id()
            conn.execute(
                "INSERT INTO file_group (id, member_count, min_distance, has_identical, status, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (gid, len(ng.members), ng.min_distance, 1 if ng.has_identical else 0, ng.status, now),
            )
            conn.executemany(
                "INSERT INTO file_group_member (group_id, file_id, decision) VALUES (?, ?, ?)",
                [(gid, f, ng.decisions.get(f, "undecided")) for f in sorted(ng.members)],
            )
            created.append(gid)
    return created


def set_decisions(conn: sqlite3.Connection, group_id: bytes, decisions: dict[bytes, str]) -> None:
    with conn:
        conn.executemany(
            "UPDATE file_group_member SET decision = ? WHERE group_id = ? AND file_id = ?",
            [(d, group_id, f) for f, d in decisions.items()],
        )
        conn.execute("UPDATE file_group SET updated_at = ? WHERE id = ?", (now_iso(), group_id))


def set_status(conn: sqlite3.Connection, group_id: bytes, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with conn:
        conn.execute(
            "UPDATE file_group SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), group_id)
        )


def list_groups(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    folder_id: bytes | None = None,
    has_identical: bool | None = None,
    min_size: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where, params = [], []
    if status is not None:
        where.append("g.status = ?")
        params.append(status)
    if has_identical is not None:
        where.append("g.has_identical = ?")
        params.append(1 if has_identical else 0)
    if min_size is not None:
        where.append("g.member_count >= ?")
        params.append(min_size)
    if folder_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM file_group_member m JOIN file f ON f.id = m.file_id"
            " WHERE m.group_id = g.id AND f.folder_id = ?)"
        )
        params.append(folder_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return conn.execute(
        f"SELECT g.* FROM file_group g {clause} ORDER BY g.updated_at DESC, g.rowid DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM file_group GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def group_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["has_identical"] = bool(row["has_identical"])
    return d


def member_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["file_id"] = id_to_str(row["file_id"])
    d["folder_id"] = id_to_str(row["folder_id"])
    d["sha1"] = row["sha1"].hex() if row["sha1"] else None
    d["relative_path"] = f"{row['directory']}/{row['name']}" if row["directory"] else row["name"]
    d["absolute_path"] = f"{row['folder_path']}/{d['relative_path']}"
    return d
```

`visualstore/db/actions.py`:

```python
"""Action table: audit trail and the memory the trash needs for restore (design §5.1 Action)."""

from __future__ import annotations

import json
import sqlite3

from visualstore.clock import now_iso
from visualstore.ids import id_to_str, new_id

TYPES = ("trash", "restore", "move", "rename", "purge")


def record(conn: sqlite3.Connection, type: str, file_id: bytes | None, before: dict, after: dict) -> bytes:
    if type not in TYPES:
        raise ValueError(f"action type must be one of {TYPES}")
    action_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO action (id, type, file_id, before, after, at) VALUES (?, ?, ?, ?, ?, ?)",
            (action_id, type, file_id, json.dumps(before), json.dumps(after), now_iso()),
        )
    return action_id


def get(conn: sqlite3.Connection, action_id: bytes) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM action WHERE id = ?", (action_id,)).fetchone()


def list_trash(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action WHERE type = 'trash' AND superseded_at IS NULL"
        " ORDER BY at DESC, rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()


def trash_older_than(conn: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action WHERE type = 'trash' AND superseded_at IS NULL AND at < ? ORDER BY at",
        (cutoff_iso,),
    ).fetchall()


def supersede(conn: sqlite3.Connection, action_id: bytes) -> None:
    with conn:
        conn.execute("UPDATE action SET superseded_at = ? WHERE id = ?", (now_iso(), action_id))


def action_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["id"] = id_to_str(row["id"])
    d["file_id"] = id_to_str(row["file_id"]) if row["file_id"] else None
    d["before"] = json.loads(row["before"])
    d["after"] = json.loads(row["after"])
    return d
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_db_pairs_groups.py -q` — Expected: 4 passed.

```bash
git add visualstore/db/pairs.py visualstore/db/groups.py visualstore/db/actions.py tests/unit/test_db_pairs_groups.py
git commit -m "feat: pair, group, dirty-queue and action queries

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Io jobs get their own connection; compute results get a sink

**Files:**
- Modify: `visualstore/db/connection.py`, `visualstore/jobs/scheduler.py`
- Create: `visualstore/jobs/results.py`, `tests/unit/test_result_sinks.py`
- Modify: `tests/unit/test_migrate.py` (busy timeout), `tests/unit/test_scheduler.py`

**Interfaces:**
- `connect()` sets `PRAGMA busy_timeout = 30000` (via `timeout=30`).
- `IoContext.open_metadata() -> sqlite3.Connection` — a fresh connection to `paths.metadata_db` for use inside the handler's thread; the handler closes it (`with closing(ctx.open_metadata()) as conn:`).
- `visualstore.jobs.results`: `ResultSink = Callable[[sqlite3.Connection, str, dict, dict], None]` called as `sink(conn, profile, params, result)` on the event-loop thread; `RESULT_SINKS: dict[str, ResultSink]` (empty until Task 8 adds `fingerprint`).
- Scheduler: on a compute `result` message, if the job's type has a sink, run it before `mark_done`; a sink exception → `record_failure` with the error (the compute work is idempotent, so the retry recomputes).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_migrate.py` inside `test_connect_applies_pragmas`: `assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000`.

`tests/unit/test_result_sinks.py`:

```python
import pytest

from visualstore.db import jobs as jobs_db
from visualstore.jobs import results
from visualstore.jobs.compute.protocol import ComputeMessage
from visualstore.jobs.scheduler import Scheduler
from visualstore.profiles import ProfileRegistry

from tests.unit.test_scheduler import FakePool


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    yield reg
    reg.close()


async def test_sink_runs_before_done_and_sees_params_and_result(registry, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(results.RESULT_SINKS, "ping", lambda conn, profile, params, result: seen.append((profile, params, result)))
    pool = FakePool()
    sched = Scheduler(registry, pool, io_handlers={}, backup_dir=tmp_path / "b", plan_interval=1000)
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute", params={"echo": "hi"})
    await sched.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "result", {"pong": "hi"}))
    await sched.tick()
    assert seen == [("a", {"echo": "hi"}, {"pong": "hi"})]
    assert jobs_db.get(conn, job_id)["state"] == "done"


async def test_sink_failure_counts_as_a_failed_attempt(registry, tmp_path, monkeypatch):
    def explode(conn, profile, params, result):
        raise RuntimeError("sink broke")

    monkeypatch.setitem(results.RESULT_SINKS, "ping", explode)
    pool = FakePool()
    sched = Scheduler(registry, pool, io_handlers={}, backup_dir=tmp_path / "b", plan_interval=1000)
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute")
    await sched.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "result", {}))
    await sched.tick()
    row = jobs_db.get(conn, job_id)
    assert row["attempts"] == 1 and "sink broke" in row["error"]


async def test_io_context_opens_its_own_connection(registry, tmp_path):
    captured = {}

    def handler(ctx):
        from contextlib import closing

        with closing(ctx.open_metadata()) as c:
            captured["same_object"] = c is registry.conn("a")
            captured["count"] = c.execute("SELECT COUNT(*) FROM job").fetchone()[0]
        return {}

    sched = Scheduler(registry, FakePool(), io_handlers={"probe": handler}, backup_dir=tmp_path / "b", plan_interval=1000)
    jobs_db.enqueue(registry.conn("a"), type="probe", kind="io")
    await sched.tick()
    await sched.wait_io()
    assert captured == {"same_object": False, "count": 1}
```

Run: `uv run pytest tests/unit/test_result_sinks.py tests/unit/test_migrate.py -q` — Expected: FAIL (`visualstore.jobs.results` missing; `open_metadata` missing; busy_timeout 0).

- [ ] **Step 2: Implement**

`visualstore/db/connection.py`: change `sqlite3.connect(path, check_same_thread=False)` to `sqlite3.connect(path, check_same_thread=False, timeout=30)` and extend the comment: "Io job handlers open a second connection to the same file from their worker thread, so writers may briefly wait for each other; 30 s is far above any transaction here."

`visualstore/jobs/results.py`:

```python
"""Result sinks: how a compute result becomes database rows.

Compute children return plain data (design §3). The scheduler calls the sink for the job's type
on the event-loop thread with the registry connection, so all database writes stay on one thread.
Milestone 2 registers `fingerprint`; milestone 3 adds `match`, `rebuild_index`, `eval`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

ResultSink = Callable[[sqlite3.Connection, str, dict, dict], None]

RESULT_SINKS: dict[str, ResultSink] = {}
```

In `visualstore/jobs/scheduler.py`:

- `IoContext` gains:

```python
    def open_metadata(self) -> sqlite3.Connection:
        """A connection of the handler's own, for use inside the worker thread. Close it."""
        from visualstore.db.connection import connect

        return connect(self.paths.metadata_db)
```

- import `from visualstore.jobs.results import RESULT_SINKS` and change `_finish` into an instance method that applies the sink:

```python
    def _finish(self, conn: sqlite3.Connection, job_id: bytes, result: dict, profile: str) -> None:
        row = jobs_db.get(conn, job_id)
        if row is None:
            return
        sink = RESULT_SINKS.get(row["type"])
        if sink is not None:
            try:
                sink(conn, profile, json.loads(row["params"]), result)
            except Exception as exc:  # noqa: BLE001 - the attempt failed; compute is idempotent
                log.exception("result sink for %s failed", row["type"])
                jobs_db.record_failure(conn, job_id, f"result sink: {type(exc).__name__}: {exc}")
                return
        progress = json.loads(row["progress"])
        progress["result"] = result
        jobs_db.set_progress(conn, job_id, progress)
        jobs_db.mark_done(conn, job_id)
```

Update the two call sites: `self._finish(conn, job_id, msg.payload, profile)` in `_drain_pool`, and `self._finish(conn, job_id, result, profile)` in `_run_io` (io results have no sinks today, but the same path keeps one definition of "done").

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit -q` — Expected: all passed (including the existing scheduler tests).

```bash
git add visualstore/db/connection.py visualstore/jobs/scheduler.py visualstore/jobs/results.py tests/unit/test_result_sinks.py tests/unit/test_migrate.py
git commit -m "feat: io jobs open their own connection; compute results flow through sinks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `fingerprint` compute handler (SHA-1)

**Files:**
- Modify: `visualstore/jobs/compute/handlers.py`, `visualstore/jobs/types.py`
- Create: `tests/unit/test_fingerprint_handler.py`

**Interfaces:**
- Job type `fingerprint`, kind `compute`. Params: `{"files": [{"id": "<uuid>", "path": "<absolute>", "media_type": "image|animated|video|other"}]}`. Result: `{"files": [{"id", "sha1": "<hex>" | null, "state": "fingerprinted" | "unreadable" | "missing", "size": int}]}`. Progress every 8 files: `{"done", "total", "message": "<last file name>"}`.
- `handlers.sha1_of(path: str) -> bytes` streaming 1 MiB chunks. Milestone 3 extends this handler with decode / vectors / thumbnail per media type; the result shape gains keys, existing keys stay.
- `KIND_OF` gains `fingerprint: compute`, `scan: io`, `regroup: io`, `apply_action: io`, `purge_trash: io`, `archive_move: io`, `import_v1: io`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_fingerprint_handler.py`:

```python
import hashlib

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
    assert by_id["A"] == {"id": "A", "sha1": hashlib.sha1(b"aaa").hexdigest(), "state": "fingerprinted", "size": 3}
    assert by_id["B"]["sha1"] == hashlib.sha1(b"bbb").hexdigest()
    assert by_id["C"] == {"id": "C", "sha1": None, "state": "missing", "size": 0}
    assert reports[-1]["done"] == 3 and reports[-1]["total"] == 3


def test_unreadable_file_is_reported_not_raised(tmp_path):
    p = tmp_path / "locked.jpg"
    p.write_bytes(b"secret")
    p.chmod(0)
    try:
        [entry] = fingerprint({"files": [{"id": "L", "path": str(p), "media_type": "image"}]}, lambda _: None)["files"]
    finally:
        p.chmod(0o600)
    assert entry["state"] == "unreadable" and entry["sha1"] is None


def test_registered():
    assert HANDLERS["fingerprint"] is fingerprint
    assert KIND_OF["fingerprint"] == "compute"
    for t in ("scan", "regroup", "apply_action", "purge_trash", "archive_move", "import_v1"):
        assert KIND_OF[t] == "io"
```

Run: `uv run pytest tests/unit/test_fingerprint_handler.py -q` — Expected: FAIL (`fingerprint`, `sha1_of` missing). The unreadable test is skipped automatically when running as root (add `@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")`).

- [ ] **Step 2: Implement**

Add to `visualstore/jobs/compute/handlers.py`:

```python
import hashlib

_CHUNK = 1024 * 1024


def sha1_of(path: str) -> bytes:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.digest()


def fingerprint(params: dict, report: Report) -> dict:
    """Milestone 2: SHA-1 only. Milestone 3 adds decode, vectors and thumbnails per media type."""
    entries = params.get("files", [])
    out = []
    for i, entry in enumerate(entries, start=1):
        path = entry["path"]
        try:
            size = os.stat(path).st_size
            digest = sha1_of(path)
            out.append({"id": entry["id"], "sha1": digest.hex(), "state": "fingerprinted", "size": size})
        except FileNotFoundError:
            out.append({"id": entry["id"], "sha1": None, "state": "missing", "size": 0})
        except OSError:
            out.append({"id": entry["id"], "sha1": None, "state": "unreadable", "size": 0})
        if i % 8 == 0 or i == len(entries):
            report({"done": i, "total": len(entries), "message": os.path.basename(path)})
    return {"files": out}
```

and register `"fingerprint": fingerprint` in `HANDLERS`. In `visualstore/jobs/types.py` extend `KIND_OF`:

```python
    "fingerprint": "compute",
    "scan": "io",
    "regroup": "io",
    "apply_action": "io",
    "purge_trash": "io",
    "archive_move": "io",
    "import_v1": "io",
```

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_fingerprint_handler.py tests/integration/test_compute_pool.py -q` — Expected: all passed.

```bash
git add visualstore/jobs/compute/handlers.py visualstore/jobs/types.py tests/unit/test_fingerprint_handler.py
git commit -m "feat: sha1 fingerprint compute handler and milestone 2 job types

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Fingerprint result sink — file state, sha1 pairs, dirty marks

**Files:**
- Modify: `visualstore/jobs/results.py`
- Create: `tests/unit/test_sink_fingerprint.py`

**Interfaces:**
- `results.sink_fingerprint(conn, profile, params, result)`: for each result entry whose file row still exists: `files.set_fingerprint_result`; for `fingerprinted` entries, bucket by sha1 (`files.files_by_sha1`), connect every bucket member to the earliest one with `pairs.upsert_sha1_pair` (star, design §7.4), `groups.mark_dirty` for the whole bucket when it has ≥ 2 members. Afterwards, if anything was marked dirty and no `regroup` job is active, `jobs_db.enqueue(type="regroup", kind="io", priority=PRIORITY_INBOX)`.
- Registered as `RESULT_SINKS["fingerprint"]`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_sink_fingerprint.py`:

```python
import pytest

from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir
from visualstore.ids import id_to_str
from visualstore.jobs.results import RESULT_SINKS, sink_fingerprint

SHA = "ab" * 20


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "p.db")
    migrate(conn, migrations_dir("profile"))
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    ids = [files.insert_file(conn, folder_id=folder, directory="", name=f"{i}.jpg", extension_id=ext, size=1, mtime=1) for i in range(3)]
    return conn, ids


def entry(fid, sha=SHA, state="fingerprinted"):
    return {"id": id_to_str(fid), "sha1": sha, "state": state, "size": 1}


def test_states_pairs_dirty_and_regroup_job(db):
    conn, (a, b, c) = db
    sink_fingerprint(conn, "p", {}, {"files": [entry(a), entry(b, state="unreadable", sha=None)]})
    assert files.get_file(conn, a)["sha1"] == bytes.fromhex(SHA) and files.get_file(conn, a)["state"] == "fingerprinted"
    assert files.get_file(conn, b)["state"] == "unreadable" and files.get_file(conn, b)["sha1"] is None
    assert pairs.pairs_of_file(conn, a) == [] and groups.dirty_count(conn) == 0
    assert not jobs_db.has_active(conn, "regroup")

    sink_fingerprint(conn, "p", {}, {"files": [entry(c)]})
    [p] = pairs.pairs_of_file(conn, c)
    assert {p["file_a"], p["file_b"]} == {a, c} and p["source"] == "sha1"
    assert set(groups.pop_dirty(conn)) == {a, c}
    assert jobs_db.has_active(conn, "regroup")
    assert jobs_db.next_queued(conn, "io")["priority"] == 50


def test_star_topology_and_idempotence(db):
    conn, (a, b, c) = db
    sink_fingerprint(conn, "p", {}, {"files": [entry(a), entry(b), entry(c)]})
    all_pairs = pairs.edges_touching(conn, [a, b, c])
    assert len(all_pairs) == 2  # a-b, a-c: a is the hub (earliest), not a complete graph
    sink_fingerprint(conn, "p", {}, {"files": [entry(a), entry(b), entry(c)]})
    assert len(pairs.edges_touching(conn, [a, b, c])) == 2
    assert jobs_db.count_by_state(conn)["queued"] == 1  # only one regroup job queued


def test_missing_row_is_skipped(db):
    conn, _ = db
    sink_fingerprint(conn, "p", {}, {"files": [{"id": "00000000-0000-0000-0000-000000000000", "sha1": SHA, "state": "fingerprinted", "size": 1}]})


def test_registered():
    assert RESULT_SINKS["fingerprint"] is sink_fingerprint
```

Run: `uv run pytest tests/unit/test_sink_fingerprint.py -q` — Expected: FAIL (`sink_fingerprint` missing).

- [ ] **Step 2: Implement**

Append to `visualstore/jobs/results.py`:

```python
from visualstore.db import files as files_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.ids import id_from_str
from visualstore.jobs.types import PRIORITY_INBOX


def sink_fingerprint(conn: sqlite3.Connection, profile: str, params: dict, result: dict) -> None:
    dirty = False
    for entry in result.get("files", []):
        try:
            file_id = id_from_str(entry["id"])
        except ValueError:
            continue
        if files_db.get_file(conn, file_id) is None:
            continue  # deleted or trashed since the job was queued
        sha1 = bytes.fromhex(entry["sha1"]) if entry.get("sha1") else None
        files_db.set_fingerprint_result(conn, file_id, sha1=sha1, state=entry["state"])
        if sha1 is None or entry["state"] != "fingerprinted":
            continue
        bucket = [r for r in files_db.files_by_sha1(conn, sha1) if r["state"] == "fingerprinted"]
        if len(bucket) < 2:
            continue
        hub = bytes(bucket[0]["id"])
        for row in bucket[1:]:
            pairs_db.upsert_sha1_pair(conn, hub, bytes(row["id"]))
        groups_db.mark_dirty(conn, [bytes(r["id"]) for r in bucket])
        dirty = True
    if dirty and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)


RESULT_SINKS["fingerprint"] = sink_fingerprint
```

(Place the imports at the top of the module with the others; the `RESULT_SINKS = {}` line must precede the registration.)

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit/test_sink_fingerprint.py tests/unit/test_result_sinks.py -q` — Expected: all passed.

```bash
git add visualstore/jobs/results.py tests/unit/test_sink_fingerprint.py
git commit -m "feat: fingerprint result sink writes sha1 pairs and queues regrouping

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `scan` io job

**Files:**
- Create: `visualstore/jobs/io/scan.py`, `tests/integration/test_scan_job.py`
- Modify: `visualstore/jobs/io/__init__.py`

**Interfaces:**
- Job `scan`, params `{"folder_id": "<uuid>"}`. `run_scan(ctx) -> dict` returning `{"inserted", "changed", "unchanged", "moved", "deleted", "fingerprint_jobs"}`.
- `scan.walk(folder_path: str, extensions: dict[str, Row], ignored: list[tuple[str, bool]], report) -> list[DiskEntry]` — `os.scandir`, no symlink following, skips directories per `should_skip_directory`, keeps only files whose lowercase extension is enabled, progress every 1000 files.
- After reconciling, enqueues `fingerprint` jobs for every `state = 'new'` file of the Folder in batches of 64, priority `PRIORITY_INBOX` for inbox Folders and `PRIORITY_ARCHIVE` for archive Folders (a user-triggered scan passes `{"priority": 100}` in its params and the batches inherit it).
- Registered in `IO_HANDLERS["scan"]`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_scan_job.py`:

```python
import hashlib
import json

import pytest

from visualstore.db import files, folders, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    (lib / "Thumbs").mkdir()
    (lib / ".visualstore-trash" / "2026-11").mkdir(parents=True)
    (lib / "sub" / "one.jpg").write_bytes(b"one")
    (lib / "two.PNG").write_bytes(b"two")
    (lib / "notes.txt").write_bytes(b"no")
    (lib / "Thumbs" / "t.jpg").write_bytes(b"t")
    (lib / ".visualstore-trash" / "2026-11" / "old.jpg").write_bytes(b"old")
    conn = reg.conn("a")
    folder = folders.create_folder(conn, name="Lib", path=str(lib), role="inbox", compare_scope="all")
    folders.add_ignored(conn, folder, "Thumbs")
    yield reg, conn, folder, lib
    reg.close()


def ctx_for(reg, folder, priority=None):
    conn = reg.conn("a")
    params = {"folder_id": id_to_str(folder)}
    if priority is not None:
        params["priority"] = priority
    return IoContext(
        profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path,
        settings=settings_db.all(conn), params=params, report=lambda p: None,
    )


def rel_paths(conn, folder):
    return sorted(files.relative_path(r["directory"], r["name"]) for r in files.list_files_in_folder(conn, folder))


def test_first_scan_inserts_media_files_only_and_queues_fingerprints(world):
    reg, conn, folder, lib = world
    result = run_scan(ctx_for(reg, folder))
    assert result["inserted"] == 2 and result["deleted"] == 0 and result["fingerprint_jobs"] == 1
    assert rel_paths(conn, folder) == ["sub/one.jpg", "two.PNG"]
    [job] = jobs_db.list_jobs(conn, state="queued")
    assert job["type"] == "fingerprint" and job["kind"] == "compute" and job["priority"] == 50
    params = json.loads(job["params"])
    assert sorted(f["path"] for f in params["files"]) == [str(lib / "sub" / "one.jpg"), str(lib / "two.PNG")]
    assert all(f["media_type"] == "image" for f in params["files"])
    assert IO_HANDLERS["scan"] is run_scan


def test_rescan_is_a_no_op_and_user_priority_propagates(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    result = run_scan(ctx_for(reg, folder, priority=100))
    assert (result["inserted"], result["changed"], result["deleted"]) == (0, 0, 0) and result["unchanged"] == 2
    assert jobs_db.list_jobs(conn, state="queued")[0]["priority"] == 100


def test_moved_file_keeps_id_sha1_and_pairs(world):
    """Review Focus 2."""
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    two = files.find_file(conn, folder, "", "two.PNG")
    files.set_fingerprint_result(conn, one["id"], sha1=hashlib.sha1(b"one").digest(), state="fingerprinted")
    files.set_fingerprint_result(conn, two["id"], sha1=hashlib.sha1(b"two").digest(), state="fingerprinted")
    pairs.upsert_sha1_pair(conn, one["id"], two["id"])  # pretend they were related

    (lib / "moved").mkdir()
    (lib / "sub" / "one.jpg").rename(lib / "moved" / "renamed.jpg")
    result = run_scan(ctx_for(reg, folder))
    assert result["moved"] == 1 and result["inserted"] == 0 and result["deleted"] == 0
    moved = files.find_file(conn, folder, "moved", "renamed.jpg")
    assert moved["id"] == one["id"] and moved["sha1"] == hashlib.sha1(b"one").digest()
    assert moved["state"] == "fingerprinted"
    assert len(pairs.pairs_of_file(conn, one["id"])) == 1
    assert files.find_file(conn, folder, "sub", "one.jpg") is None
    assert jobs_db.list_jobs(conn, state="queued") == [] or all(
        json.loads(j["params"]).get("files", [{}])[0].get("id") != id_to_str(one["id"])
        for j in jobs_db.list_jobs(conn, state="queued")
    )


def test_changed_content_resets_hash_and_pairs(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    two = files.find_file(conn, folder, "", "two.PNG")
    files.set_fingerprint_result(conn, one["id"], sha1=b"\x01" * 20, state="fingerprinted")
    files.set_fingerprint_result(conn, two["id"], sha1=b"\x01" * 20, state="fingerprinted")
    pairs.upsert_sha1_pair(conn, one["id"], two["id"])
    import os, time
    (lib / "sub" / "one.jpg").write_bytes(b"one-changed")
    os.utime(lib / "sub" / "one.jpg", ns=(time.time_ns(), time.time_ns() + 10**9))
    result = run_scan(ctx_for(reg, folder))
    assert result["changed"] == 1
    row = files.get_file(conn, one["id"])
    assert row["state"] == "new" and row["sha1"] is None and row["size"] == len(b"one-changed")
    assert pairs.pairs_of_file(conn, one["id"]) == []


def test_deleted_file_is_removed_and_ambiguous_copies_are_not_moves(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    files.set_fingerprint_result(conn, one["id"], sha1=hashlib.sha1(b"one").digest(), state="fingerprinted")
    (lib / "sub" / "one.jpg").unlink()
    (lib / "copy1.jpg").write_bytes(b"one")
    (lib / "copy2.jpg").write_bytes(b"one")
    (lib / "two.PNG").unlink()
    result = run_scan(ctx_for(reg, folder))
    assert result["moved"] == 0 and result["inserted"] == 2 and result["deleted"] == 2
    assert files.get_file(conn, one["id"]) is None
    assert rel_paths(conn, folder) == ["copy1.jpg", "copy2.jpg"]


def test_disabled_folder_is_skipped(world):
    reg, conn, folder, lib = world
    folders.update_folder(conn, folder, enabled=False)
    assert run_scan(ctx_for(reg, folder)) == {"skipped": "folder disabled"}
```

Run: `uv run pytest tests/integration/test_scan_job.py -q` — Expected: FAIL (`visualstore.jobs.io.scan` missing).

- [ ] **Step 2: Implement**

`visualstore/jobs/io/scan.py`:

```python
"""Scan job (design §7.1): reconcile a Folder's directory tree with the file table.

Runs in the scheduler's worker thread with its own connection. Pure decisions live in
domain.scan; this module does the walking, hashing of move candidates, and the writes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import closing

from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.scan import DbFile, DiskEntry, diff, move_candidates, resolve_moves, should_skip_directory
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs.compute.handlers import sha1_of
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX

FINGERPRINT_BATCH = 64


def walk(
    folder_path: str, extensions: dict, ignored: list[tuple[str, bool]], report: Callable[[dict], None]
) -> list[DiskEntry]:
    out: list[DiskEntry] = []
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        if should_skip_directory(rel_dir, ignored):
            continue
        abs_dir = os.path.join(folder_path, rel_dir) if rel_dir else folder_path
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                stack.append(f"{rel_dir}/{entry.name}" if rel_dir else entry.name)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            _, dot, ext = entry.name.rpartition(".")
            if not dot or ext.lower() not in extensions:
                continue
            st = entry.stat(follow_symlinks=False)
            out.append(DiskEntry(rel_dir, entry.name, ext.lower(), st.st_size, st.st_mtime_ns))
            if len(out) % 1000 == 0:
                report({"done": len(out), "total": 0, "message": f"listed {len(out)} files"})
    return out


def run_scan(ctx: IoContext) -> dict:
    folder_id = id_from_str(ctx.params["folder_id"])
    with closing(ctx.open_metadata()) as conn:
        folder = folders_db.get_folder(conn, folder_id)
        if folder is None:
            return {"skipped": "folder missing"}
        if not folder["enabled"]:
            return {"skipped": "folder disabled"}
        extensions = folders_db.extension_map(conn)
        ignored = [(r["directory"], bool(r["recursive"])) for r in folders_db.list_ignored(conn, folder_id)]
        disk = walk(folder["path"], extensions, ignored, ctx.report)
        db_rows = files_db.list_files_in_folder(conn, folder_id)
        plan = diff(
            disk,
            [DbFile(bytes(r["id"]), r["directory"], r["name"], r["size"], r["mtime"], r["sha1"]) for r in db_rows],
        )
        ctx.report({"done": 0, "total": 0, "message": f"{len(plan.insert)} new, {len(plan.missing)} missing"})

        inserted: dict[tuple[str, str], bytes] = {}
        for e in plan.insert:
            inserted[(e.directory, e.name)] = files_db.insert_file(
                conn, folder_id=folder_id, directory=e.directory, name=e.name,
                extension_id=bytes(extensions[e.extension]["id"]), size=e.size, mtime=e.mtime,
            )
        for fid, e in plan.changed:
            files_db.update_stat(conn, fid, e.size, e.mtime)
            files_db.reset_for_recompute(conn, fid)

        moved = 0
        candidates = move_candidates(plan)
        if candidates:
            missing_sha1 = {f.id: f.sha1 for _, (olds, _) in candidates.items() for f in olds if f.sha1}
            inserted_sha1 = {}
            for _, (_, news) in candidates.items():
                for e in news:
                    try:
                        inserted_sha1[(e.directory, e.name)] = sha1_of(
                            os.path.join(folder["path"], e.directory, e.name) if e.directory
                            else os.path.join(folder["path"], e.name)
                        )
                    except OSError:
                        pass
            moves = resolve_moves(missing_sha1, inserted_sha1)
            for old_id, (directory, name) in moves:
                new_id_ = inserted.pop((directory, name))
                entry = next(e for e in plan.insert if (e.directory, e.name) == (directory, name))
                files_db.delete_files(conn, [new_id_])
                files_db.update_path(conn, old_id, directory, name)
                files_db.update_stat(conn, old_id, entry.size, entry.mtime)
                moved += 1
            moved_ids = {old for old, _ in moves}
        else:
            moved_ids = set()

        to_delete = [f.id for f in plan.missing if f.id not in moved_ids]
        files_db.delete_files(conn, to_delete)

        priority = int(ctx.params.get("priority") or (PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE))
        jobs = _enqueue_fingerprints(conn, folder, priority)
        return {
            "inserted": len(plan.insert) - moved,
            "changed": len(plan.changed),
            "unchanged": plan.unchanged,
            "moved": moved,
            "deleted": len(to_delete),
            "fingerprint_jobs": jobs,
        }


def _enqueue_fingerprints(conn, folder, priority: int) -> int:
    extensions = {bytes(r["id"]): r["media_type"] for r in folders_db.list_extensions(conn)}
    pending = files_db.files_by_state(conn, "new", bytes(folder["id"]), limit=1_000_000)
    count = 0
    for start in range(0, len(pending), FINGERPRINT_BATCH):
        batch = pending[start : start + FINGERPRINT_BATCH]
        jobs_db.enqueue(
            conn, type="fingerprint", kind="compute", priority=priority,
            params={"files": [
                {
                    "id": id_to_str(r["id"]),
                    "path": f"{folder['path']}/{r['directory']}/{r['name']}" if r["directory"] else f"{folder['path']}/{r['name']}",
                    "media_type": extensions.get(bytes(r["extension_id"]), "other"),
                }
                for r in batch
            ]},
        )
        count += 1
    return count
```

Register in `visualstore/jobs/io/__init__.py`: `from visualstore.jobs.io.scan import run_scan` and `"scan": run_scan` in `IO_HANDLERS`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_scan_job.py -q` — Expected: 6 passed.

```bash
git add visualstore/jobs/io/scan.py visualstore/jobs/io/__init__.py tests/integration/test_scan_job.py
git commit -m "feat: scan job with ignored directories, move detection and fingerprint batching

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: `regroup` io job with automatic execution of identical groups

**Files:**
- Create: `visualstore/jobs/io/regroup.py`, `tests/integration/test_regroup_job.py`
- Modify: `visualstore/jobs/io/__init__.py`

**Interfaces:**
- Job `regroup`, params `{}`. `run_regroup(ctx) -> dict` → `{"components": n, "groups": n, "auto_executed": n}`.
- Algorithm: drain `regroup_queue` in batches; expand each batch to the closure over pairs and current group memberships; run `domain.grouping.components`; for every component build `Member`s and call `domain.resolution.preselect(all_identical=<every edge inside the component is sha1>, auto_execute_identical=settings["resolution.auto_execute_identical"])`; **status preservation**: a component whose member set equals an existing group's member set keeps that group's status and decisions when the status is `resolved` or `dismissed`; a component whose every edge carries `judgment = related_keep` is `resolved` with all members `keep` (nothing left to decide); otherwise the preselection's status and decisions. Old groups of every file in scope are replaced in one transaction. Then, for each new group with `auto_executable`, enqueue `apply_action` `{"group_id": ..., "trash": [<ids with decision delete>]}` at `PRIORITY_INBOX` and mark it `auto_resolved`.
- `group_distance` comes from `settings["grouping.group_distance"]` (added in Task 14; use `.get(..., 0.2)` until then).
- Registered in `IO_HANDLERS["regroup"]`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_regroup_job.py`:

```python
import json

import pytest

from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.regroup import run_regroup
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry

SHA = b"\x05" * 20


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    for d in ("arch", "arch2", "inbox"):
        (tmp_path / d).mkdir()
    arch = folders.create_folder(conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all", priority=1)
    arch2 = folders.create_folder(conn, name="Arch2", path=str(tmp_path / "arch2"), role="archive", compare_scope="all", priority=2)
    inbox = folders.create_folder(conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all")
    yield reg, conn, {"arch": arch, "arch2": arch2, "inbox": inbox}
    reg.close()


def add(conn, folder, name, sha=SHA):
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(conn, folder_id=folder, directory="", name=name, extension_id=ext, size=1, mtime=1)
    files.set_fingerprint_result(conn, fid, sha1=sha, state="fingerprinted")
    return fid


def connect(conn, *ids):
    hub = ids[0]
    for other in ids[1:]:
        pairs.upsert_sha1_pair(conn, hub, other)
    groups.mark_dirty(conn, ids)


def ctx_for(reg, **overrides):
    conn = reg.conn("a")
    settings = settings_db.all(conn)
    settings.update(overrides)
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path, settings=settings, params={}, report=lambda p: None)


def test_archive_plus_inbox_identical_group_is_auto_executed(world):
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    result = run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": True}))
    assert result == {"components": 1, "groups": 1, "auto_executed": 1}
    g = groups.group_of_file(conn, a)
    assert g["status"] == "auto_resolved" and g["has_identical"] == 1
    decisions = {m["file_id"]: m["decision"] for m in groups.members(conn, g["id"])}
    assert decisions == {a: "keep", b: "delete"}
    [job] = jobs_db.list_jobs(conn, state="queued")
    assert job["type"] == "apply_action" and json.loads(job["params"])["trash"] == [b.hex() if False else __import__("visualstore.ids", fromlist=["id_to_str"]).id_to_str(b)]
    assert groups.dirty_count(conn) == 0
    assert IO_HANDLERS["regroup"] is run_regroup


def test_setting_off_and_archive_only_groups_stay_open(world):
    """Review Focus 4."""
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg")
    connect(conn, a, b)
    run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": False}))
    assert groups.group_of_file(conn, a)["status"] == "open"
    assert jobs_db.list_jobs(conn, state="queued") == []

    c, d = add(conn, f["arch"], "c.jpg", sha=b"\x06" * 20), add(conn, f["arch"], "d.jpg", sha=b"\x06" * 20)
    connect(conn, c, d)
    run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": True}))
    g = groups.group_of_file(conn, c)
    assert g["status"] == "open" and {m["decision"] for m in groups.members(conn, g["id"])} == {"undecided"}
    assert jobs_db.list_jobs(conn, state="queued") == []


def test_unrelated_judgment_splits_and_related_keep_closure_resolves(world):
    reg, conn, f = world
    a, b, c = add(conn, f["arch"], "a.jpg"), add(conn, f["arch2"], "b.jpg"), add(conn, f["arch"], "c.jpg")
    connect(conn, a, b, c)
    run_regroup(ctx_for(reg))
    g1 = groups.group_of_file(conn, a)
    assert g1["member_count"] == 3

    pairs.set_judgment(conn, a, c, "unrelated", "user")
    groups.mark_dirty(conn, [a, c])
    run_regroup(ctx_for(reg))
    assert groups.get_group(conn, g1["id"]) is None
    g2 = groups.group_of_file(conn, a)
    assert {m["file_id"] for m in groups.members(conn, g2["id"])} == {a, b}
    assert groups.group_of_file(conn, c) is None  # singleton, no group

    pairs.set_judgment(conn, a, b, "related_keep", "user")
    groups.mark_dirty(conn, [a])
    run_regroup(ctx_for(reg))
    g3 = groups.group_of_file(conn, a)
    assert g3["status"] == "resolved" and {m["decision"] for m in groups.members(conn, g3["id"])} == {"keep"}


def test_resolved_status_survives_regroup_of_the_same_members(world):
    reg, conn, f = world
    a, b = add(conn, f["arch"], "a.jpg"), add(conn, f["arch"], "b.jpg")
    connect(conn, a, b)
    run_regroup(ctx_for(reg))
    g = groups.group_of_file(conn, a)
    groups.set_decisions(conn, g["id"], {a: "keep", b: "keep"})
    groups.set_status(conn, g["id"], "dismissed")
    groups.mark_dirty(conn, [a])
    run_regroup(ctx_for(reg))
    g2 = groups.group_of_file(conn, a)
    assert g2["status"] == "dismissed" and {m["decision"] for m in groups.members(conn, g2["id"])} == {"keep"}


def test_member_deletion_shrinks_or_dissolves_the_group(world):
    reg, conn, f = world
    a, b, c = add(conn, f["arch"], "a.jpg"), add(conn, f["inbox"], "b.jpg"), add(conn, f["inbox"], "c.jpg")
    connect(conn, a, b, c)
    run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": False}))
    files.delete_files(conn, [c])
    run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": False}))
    assert groups.group_of_file(conn, a)["member_count"] == 2
    files.delete_files(conn, [b])
    run_regroup(ctx_for(reg, **{"resolution.auto_execute_identical": False}))
    assert groups.group_of_file(conn, a) is None
    assert groups.count_by_status(conn)["open"] == 0
```

Replace the awkward expression in the first test's `assert job["type"] == ...` line with: `assert job["type"] == "apply_action" and json.loads(job["params"])["trash"] == [id_to_str(b)]` and add `from visualstore.ids import id_to_str` to the imports.

Run: `uv run pytest tests/integration/test_regroup_job.py -q` — Expected: FAIL, module missing.

- [ ] **Step 2: Implement**

`visualstore/jobs/io/regroup.py`:

```python
"""Regroup job (design §7.5): recompute connected components for dirty files, preselect
decisions (design §8.2) and hand identical groups to apply_action when allowed."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.db.groups import NewGroup
from visualstore.domain.grouping import Component, PairEdge, components
from visualstore.domain.resolution import Member, preselect
from visualstore.ids import id_to_str
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX

BATCH = 2000


def _closure(conn: sqlite3.Connection, seeds: list[bytes]) -> set[bytes]:
    """Seeds plus everything reachable through pairs or shared group membership."""
    scope: set[bytes] = set(seeds)
    frontier: set[bytes] = set(seeds)
    while frontier:
        edges = pairs_db.edges_touching(conn, frontier)
        reached = {bytes(e["file_a"]) for e in edges} | {bytes(e["file_b"]) for e in edges}
        group_ids = {bytes(g["id"]) for f in frontier if (g := groups_db.group_of_file(conn, f)) is not None}
        reached |= set(groups_db.member_file_ids(conn, group_ids))
        frontier = reached - scope
        scope |= frontier
    return scope


def _members(conn: sqlite3.Connection, ids: set[bytes]) -> dict[bytes, Member]:
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        "SELECT f.id, f.folder_id, f.directory, f.name, fo.role, fo.priority"
        f" FROM file f JOIN folder fo ON fo.id = f.folder_id WHERE f.id IN ({marks})",
        list(ids),
    ).fetchall()
    return {
        bytes(r["id"]): Member(
            bytes(r["id"]), bytes(r["folder_id"]), r["role"], r["priority"],
            f"{r['directory']}/{r['name']}" if r["directory"] else r["name"],
        )
        for r in rows
    }


def _existing_group(conn: sqlite3.Connection, comp: Component):
    """The current group holding exactly this member set, if any (members, decisions, status)."""
    any_member = next(iter(comp.members))
    g = groups_db.group_of_file(conn, any_member)
    if g is None:
        return None
    rows = groups_db.members(conn, bytes(g["id"]))
    if {bytes(r["file_id"]) for r in rows} != set(comp.members):
        return None
    return g["status"], {bytes(r["file_id"]): r["decision"] for r in rows}


def run_regroup(ctx: IoContext) -> dict:
    group_distance = float(ctx.settings.get("grouping.group_distance", 0.2))
    auto_setting = bool(ctx.settings.get("resolution.auto_execute_identical", True))
    stats = {"components": 0, "groups": 0, "auto_executed": 0}
    with closing(ctx.open_metadata()) as conn:
        while True:
            seeds = groups_db.pop_dirty(conn, BATCH)
            if not seeds:
                break
            scope = _closure(conn, seeds)
            edge_rows = pairs_db.edges_touching(conn, scope)
            edges = [
                PairEdge(bytes(r["file_a"]), bytes(r["file_b"]), r["distance"], r["source"], r["judgment"])
                for r in edge_rows
            ]
            comps = components(scope, edges, group_distance)
            stats["components"] += len(comps)
            old_groups = {
                bytes(g["id"]) for f in scope if (g := groups_db.group_of_file(conn, f)) is not None
            }
            members_by_id = _members(conn, scope) if scope else {}
            new_groups: list[NewGroup] = []
            to_execute: list[int] = []
            for comp in comps:
                inner = [e for e in edges if e.a in comp.members and e.b in comp.members and e.judgment != "unrelated"]
                all_identical = all(e.source == "sha1" for e in inner)
                existing = _existing_group(conn, comp)
                if existing is not None and existing[0] in ("resolved", "dismissed"):
                    status, decisions, auto = existing[0], existing[1], False
                elif inner and all(e.judgment == "related_keep" for e in inner):
                    status, decisions, auto = "resolved", {m: "keep" for m in comp.members}, False
                else:
                    pre = preselect(
                        [members_by_id[m] for m in comp.members if m in members_by_id],
                        all_identical=all_identical, auto_execute_identical=auto_setting,
                    )
                    status, decisions, auto = pre.status, pre.decisions, pre.auto_executable
                if auto:
                    to_execute.append(len(new_groups))
                new_groups.append(NewGroup(comp.members, comp.min_distance, comp.has_identical, status, decisions))
            created = groups_db.replace_groups(conn, old_groups, new_groups)
            stats["groups"] += len(created)
            for index in to_execute:
                gid, ng = created[index], new_groups[index]
                jobs_db.enqueue(
                    conn, type="apply_action", kind="io", priority=PRIORITY_INBOX,
                    params={"group_id": id_to_str(gid),
                            "trash": [id_to_str(f) for f, d in ng.decisions.items() if d == "delete"]},
                )
                stats["auto_executed"] += 1
            ctx.report({"done": stats["groups"], "total": 0, "message": f"{stats['groups']} groups"})
    return stats
```

Register `"regroup": run_regroup` in `IO_HANDLERS`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_regroup_job.py -q` — Expected: 5 passed.

```bash
git add visualstore/jobs/io/regroup.py visualstore/jobs/io/__init__.py tests/integration/test_regroup_job.py
git commit -m "feat: regroup job with status preservation and automatic identical-group execution

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: `apply_action` (trash, restore) and `purge_trash` io jobs

**Files:**
- Create: `visualstore/jobs/io/actions.py`, `tests/integration/test_actions_job.py`
- Modify: `visualstore/jobs/io/__init__.py`

**Interfaces:**
- Job `apply_action`, params `{"group_id"?: str, "trash"?: [file ids], "restore"?: [action ids]}`. `run_apply_action(ctx) -> dict` → `{"trashed", "restored", "skipped": [{"id", "reason"}]}`. When `group_id` is given and every trash item succeeded, the group's status becomes `resolved` (auto-executed groups stay `auto_resolved`; the job only changes `open` → `resolved`).
- `move_file(src: Path, dest: Path) -> Path` — creates parent dirs, resolves name collisions with ` (1)`, ` (2)` suffixes before the extension, uses `os.replace`, falls back on `EXDEV` to `shutil.copy2` + size check + `os.unlink`; returns the final destination. Raises `ActionError` on any disk failure (database untouched).
- `trash_path(folder_path: str, directory: str, name: str, when: datetime) -> Path` → `<folder>/.visualstore-trash/<yyyy-mm>/<directory>/<name>`.
- `remove_empty_parents(path: Path, stop_at: Path)` — removes empty directories from `path` up to but excluding `stop_at`; never removes `.visualstore-trash` itself.
- `restore` re-inserts the file row with its **old id**, sha1, size, mtime and `state = fingerprinted` (or `new` without sha1), re-links sha1 pairs via `results.sink_fingerprint`, supersedes the trash action and records a `restore` action. A target path that already exists is skipped with reason `exists`; a Folder that no longer exists → `folder missing`.
- Job `purge_trash`, params `{}`: deletes trashed files whose action is older than `settings["trash.retention_days"]` days, records `purge`, supersedes the trash action, removes empty trash directories. Returns `{"purged": n, "missing": n}`.
- When `settings["trash.enabled"]` is false, trash deletes permanently (`os.unlink`) and records the trash action with `after = {"path": null, "permanent": true}`.
- Registered: `IO_HANDLERS["apply_action"]`, `IO_HANDLERS["purge_trash"]`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_actions_job.py`:

```python
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from visualstore.db import actions, files, folders, groups, pairs
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.actions import ActionError, move_file, remove_empty_parents, run_apply_action, run_purge_trash, trash_path
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    folder = folders.create_folder(conn, name="Lib", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(rel, content):
        directory, _, name = rel.rpartition("/")
        p = lib / rel
        p.write_bytes(content)
        st = p.stat()
        fid = files.insert_file(conn, folder_id=folder, directory=directory, name=name, extension_id=ext, size=st.st_size, mtime=st.st_mtime_ns)
        files.set_fingerprint_result(conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted")
        return fid

    yield reg, conn, folder, lib, add
    reg.close()


def ctx_for(reg, params, **overrides):
    conn = reg.conn("a")
    settings = settings_db.all(conn)
    settings.update(overrides)
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path, settings=settings, params=params, report=lambda p: None)


def test_trash_moves_file_keeps_relative_path_and_records_action(world):
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    b = add("sub/b.jpg", b"aaa")
    pairs.upsert_sha1_pair(conn, a, b)
    [gid] = groups.replace_groups(conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {a: "keep", b: "delete"})])

    result = run_apply_action(ctx_for(reg, {"group_id": id_to_str(gid), "trash": [id_to_str(b)]}))
    assert result["trashed"] == 1 and result["skipped"] == []
    month = datetime.now(UTC).strftime("%Y-%m")
    moved = lib / ".visualstore-trash" / month / "sub" / "b.jpg"
    assert moved.read_bytes() == b"aaa" and not (lib / "sub" / "b.jpg").exists()
    assert files.get_file(conn, b) is None and pairs.pairs_of_file(conn, a) == []
    [act] = actions.list_trash(conn)
    d = actions.action_to_dict(act)
    assert d["before"]["relative_path"] == "sub/b.jpg" and d["after"]["path"] == str(moved)
    assert d["before"]["sha1"] == hashlib.sha1(b"aaa").hexdigest()
    assert groups.get_group(conn, gid)["status"] == "resolved"
    assert set(groups.pop_dirty(conn)) == {a}
    assert IO_HANDLERS["apply_action"] is run_apply_action and IO_HANDLERS["purge_trash"] is run_purge_trash


def test_restore_brings_the_file_back_with_its_id_and_pairs(world):
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    b = add("sub/b.jpg", b"aaa")
    pairs.upsert_sha1_pair(conn, a, b)
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))
    [act] = actions.list_trash(conn)
    result = run_apply_action(ctx_for(reg, {"restore": [id_to_str(act["id"])]}))
    assert result["restored"] == 1
    row = files.find_file(conn, folder, "sub", "b.jpg")
    assert row["id"] == b and row["state"] == "fingerprinted" and row["sha1"] == hashlib.sha1(b"aaa").digest()
    assert (lib / "sub" / "b.jpg").read_bytes() == b"aaa"
    assert len(pairs.pairs_of_file(conn, a)) == 1  # sha1 pair re-linked
    assert actions.list_trash(conn) == [] and actions.get(conn, act["id"])["superseded_at"]
    assert any(r["type"] == "restore" for r in conn.execute("SELECT type FROM action"))

    (lib / "sub" / "b.jpg").write_bytes(b"new occupant")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))  # trash again (moves the new occupant)
    [act2] = actions.list_trash(conn)
    (lib / "sub" / "b.jpg").write_bytes(b"another")  # target taken: restore must refuse
    result = run_apply_action(ctx_for(reg, {"restore": [id_to_str(act2["id"])]}))
    assert result["restored"] == 0 and result["skipped"][0]["reason"] == "exists"
    assert actions.list_trash(conn) != []


def test_trash_name_collision_gets_a_suffix_and_disk_failure_leaves_db_intact(world, monkeypatch):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a = add("x.jpg", b"first")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a)]}))
    b = add("x.jpg", b"second")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))
    month = datetime.now(UTC).strftime("%Y-%m")
    names = sorted(p.name for p in (lib / ".visualstore-trash" / month).iterdir())
    assert names == ["x (1).jpg", "x.jpg"]

    c = add("y.jpg", b"third")

    def boom(src, dst):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ActionError):
        run_apply_action(ctx_for(reg, {"trash": [id_to_str(c)]}))
    assert files.get_file(conn, c) is not None and (lib / "y.jpg").exists()
    assert len(actions.list_trash(conn)) == 2


def test_move_file_cross_device_falls_back_to_copy(world, monkeypatch, tmp_path):
    reg, conn, folder, lib, add = world
    src = lib / "cd.jpg"
    src.write_bytes(b"payload")
    real_replace = os.replace

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", exdev)
    dest = move_file(src, tmp_path / "elsewhere" / "cd.jpg")
    assert dest.read_bytes() == b"payload" and not src.exists()
    monkeypatch.setattr(os, "replace", real_replace)


def test_permanent_delete_when_trash_disabled(world):
    reg, conn, folder, lib, add = world
    a = add("gone.jpg", b"g")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a)]}, **{"trash.enabled": False}))
    assert not (lib / "gone.jpg").exists() and not (lib / ".visualstore-trash").exists()
    [act] = conn.execute("SELECT * FROM action").fetchall()
    assert actions.action_to_dict(act)["after"] == {"path": None, "permanent": True}
    assert actions.list_trash(conn) == []  # permanent deletions are not restorable


def test_purge_removes_expired_trash_and_empty_dirs(world):
    reg, conn, folder, lib, add = world
    a = add("sub/old.jpg", b"o")
    b = add("sub/fresh.jpg", b"f")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a), id_to_str(b)]}))
    old_act = next(r for r in actions.list_trash(conn) if actions.action_to_dict(r)["before"]["name"] == "old.jpg")
    forty_days_ago = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
    conn.execute("UPDATE action SET at = ? WHERE id = ?", (forty_days_ago, old_act["id"]))
    result = run_purge_trash(ctx_for(reg, {}))
    assert result == {"purged": 1, "missing": 0}
    remaining = [actions.action_to_dict(r)["before"]["name"] for r in actions.list_trash(conn)]
    assert remaining == ["fresh.jpg"]
    assert not any(p.name == "old.jpg" for p in (lib / ".visualstore-trash").rglob("*"))
    assert not (lib / "sub").exists()  # emptied source directory cleaned up after trashing


def test_remove_empty_parents_stops_at_root_and_trash(tmp_path):
    root = tmp_path / "r"
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    remove_empty_parents(deep, stop_at=root)
    assert root.exists() and not (root / "a").exists()
    trash = root / ".visualstore-trash" / "2026-11" / "x"
    trash.mkdir(parents=True)
    remove_empty_parents(trash, stop_at=root)
    assert (root / ".visualstore-trash").exists() and not (root / ".visualstore-trash" / "2026-11").exists()


def test_trash_path_layout():
    p = trash_path("/media/lib", "a/b", "c.jpg", datetime(2026, 11, 6, tzinfo=UTC))
    assert p == Path("/media/lib/.visualstore-trash/2026-11/a/b/c.jpg")
```

Run: `uv run pytest tests/integration/test_actions_job.py -q` — Expected: FAIL, module missing.

- [ ] **Step 2: Implement**

`visualstore/jobs/io/actions.py`:

```python
"""File actions (design §8.5): trash with retention, restore, purge. Disk first, database second."""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from visualstore.db import actions as actions_db
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.domain.paths import TRASH_DIR
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs import results
from visualstore.jobs.scheduler import IoContext


class ActionError(Exception):
    pass


# -- disk primitives ---------------------------------------------------------------------


def trash_path(folder_path: str, directory: str, name: str, when: datetime) -> Path:
    base = Path(folder_path) / TRASH_DIR / when.astimezone(UTC).strftime("%Y-%m")
    return base / directory / name if directory else base / name


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 10_000):
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise ActionError(f"too many name collisions for {dest}")


def move_file(src: Path, dest: Path) -> Path:
    """Move with collision suffixes; cross-device moves copy, verify the size, then delete."""
    dest = _unique(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copy2(src, dest)
            if dest.stat().st_size != src.stat().st_size:
                dest.unlink(missing_ok=True)
                raise ActionError(f"size mismatch after copying {src} to {dest}") from exc
            os.unlink(src)
    except ActionError:
        raise
    except OSError as exc:
        raise ActionError(f"could not move {src} to {dest}: {exc}") from exc
    return dest


def remove_empty_parents(path: Path, stop_at: Path) -> None:
    current = path if path.is_dir() else path.parent
    stop_at = stop_at.resolve()
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved == stop_at or stop_at not in resolved.parents or current.name == TRASH_DIR:
            return
        try:
            current.rmdir()  # fails (and we stop) when not empty
        except OSError:
            return
        current = current.parent


# -- trash / restore -----------------------------------------------------------------------


def _trash_one(conn: sqlite3.Connection, ctx: IoContext, file_id: bytes, now: datetime) -> str | None:
    row = files_db.get_file(conn, file_id)
    if row is None:
        return "missing row"
    folder = folders_db.get_folder(conn, bytes(row["folder_id"]))
    rel = files_db.relative_path(row["directory"], row["name"])
    src = Path(folder["path"]) / rel
    if not src.is_file():
        return "missing file"
    before = {
        "file_id": id_to_str(file_id), "folder_id": id_to_str(bytes(row["folder_id"])),
        "path": str(src), "relative_path": rel, "directory": row["directory"], "name": row["name"],
        "extension_id": id_to_str(bytes(row["extension_id"])), "size": row["size"], "mtime": row["mtime"],
        "sha1": row["sha1"].hex() if row["sha1"] else None,
    }
    if ctx.settings.get("trash.enabled", True):
        dest = move_file(src, trash_path(folder["path"], row["directory"], row["name"], now))
        after = {"path": str(dest)}
    else:
        try:
            src.unlink()
        except OSError as exc:
            raise ActionError(f"could not delete {src}: {exc}") from exc
        after = {"path": None, "permanent": True}
    actions_db.record(conn, "trash", file_id, before, after)
    if after.get("permanent"):
        actions_db.supersede(conn, _latest_action_id(conn))
    files_db.delete_files(conn, [file_id])
    remove_empty_parents(src.parent, Path(folder["path"]))
    return None


def _latest_action_id(conn: sqlite3.Connection) -> bytes:
    return bytes(conn.execute("SELECT id FROM action ORDER BY rowid DESC LIMIT 1").fetchone()["id"])


def _restore_one(conn: sqlite3.Connection, ctx: IoContext, action_id: bytes) -> str | None:
    act = actions_db.get(conn, action_id)
    if act is None or act["type"] != "trash" or act["superseded_at"]:
        return "not restorable"
    d = actions_db.action_to_dict(act)
    before, after = d["before"], d["after"]
    if after.get("path") is None:
        return "permanently deleted"
    folder = folders_db.get_folder(conn, id_from_str(before["folder_id"]))
    if folder is None:
        return "folder missing"
    trashed = Path(after["path"])
    if not trashed.is_file():
        return "trash file missing"
    target = Path(folder["path"]) / before["relative_path"]
    if target.exists() or files_db.find_file(conn, bytes(folder["id"]), before["directory"], before["name"]):
        return "exists"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(trashed, target)
    except OSError as exc:
        raise ActionError(f"could not restore {trashed} to {target}: {exc}") from exc
    file_id = id_from_str(before["file_id"])
    sha1 = bytes.fromhex(before["sha1"]) if before.get("sha1") else None
    files_db.insert_file(
        conn, folder_id=bytes(folder["id"]), directory=before["directory"], name=before["name"],
        extension_id=id_from_str(before["extension_id"]), size=before["size"], mtime=before["mtime"],
        state="fingerprinted" if sha1 else "new", sha1=sha1, file_id=file_id,
    )
    if sha1:
        results.sink_fingerprint(
            conn, ctx.profile, {},
            {"files": [{"id": before["file_id"], "sha1": before["sha1"], "state": "fingerprinted", "size": before["size"]}]},
        )
    actions_db.supersede(conn, action_id)
    actions_db.record(conn, "restore", file_id, {"trash_action": id_to_str(action_id), "path": str(trashed)}, {"path": str(target)})
    remove_empty_parents(trashed.parent, Path(folder["path"]))
    return None


def run_apply_action(ctx: IoContext) -> dict:
    now = datetime.now(UTC)
    result = {"trashed": 0, "restored": 0, "skipped": []}
    with closing(ctx.open_metadata()) as conn:
        for text in ctx.params.get("trash", []):
            reason = _trash_one(conn, ctx, id_from_str(text), now)
            if reason:
                result["skipped"].append({"id": text, "reason": reason})
            else:
                result["trashed"] += 1
        for text in ctx.params.get("restore", []):
            reason = _restore_one(conn, ctx, id_from_str(text))
            if reason:
                result["skipped"].append({"id": text, "reason": reason})
            else:
                result["restored"] += 1
        group_text = ctx.params.get("group_id")
        if group_text and not result["skipped"]:
            g = groups_db.get_group(conn, id_from_str(group_text))
            if g is not None and g["status"] == "open":
                groups_db.set_status(conn, bytes(g["id"]), "resolved")
    return result


# -- purge ---------------------------------------------------------------------------------


def run_purge_trash(ctx: IoContext) -> dict:
    days = int(ctx.settings.get("trash.retention_days", 30))
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    purged = missing = 0
    with closing(ctx.open_metadata()) as conn:
        for act in actions_db.trash_older_than(conn, cutoff):
            d = actions_db.action_to_dict(act)
            path = Path(d["after"]["path"]) if d["after"].get("path") else None
            if path is not None and path.is_file():
                try:
                    path.unlink()
                except OSError as exc:
                    raise ActionError(f"could not purge {path}: {exc}") from exc
                purged += 1
            else:
                missing += 1
            actions_db.supersede(conn, bytes(act["id"]))
            actions_db.record(conn, "purge", act["file_id"], {"trash_action": d["id"], "path": str(path)}, {})
            if path is not None:
                folder = folders_db.get_folder(conn, id_from_str(d["before"]["folder_id"]))
                if folder is not None:
                    remove_empty_parents(path.parent, Path(folder["path"]))
    return {"purged": purged, "missing": missing}
```

Register `"apply_action": run_apply_action` and `"purge_trash": run_purge_trash` in `IO_HANDLERS`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_actions_job.py -q` — Expected: 8 passed.

```bash
git add visualstore/jobs/io/actions.py visualstore/jobs/io/__init__.py tests/integration/test_actions_job.py
git commit -m "feat: trash, restore and purge actions with disk-first semantics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: `archive_move` io job

**Files:**
- Create: `visualstore/jobs/io/archive_move.py`, `tests/integration/test_archive_move.py`
- Modify: `visualstore/jobs/io/__init__.py`

**Interfaces:**
- Job `archive_move`, params `{"source_folder_id", "target_folder_id", "directories"?: ["a", "a/b"]}` (relative directory prefixes; omitted = whole Folder). `run_archive_move(ctx) -> dict` → `{"moved", "conflicts": [relative paths], "skipped": reason?}`.
- Preconditions (returned as `{"skipped": "..."}` without moving anything): source role must be `inbox`, target role `archive`, and no `open` group may contain a source file within the selection (design §8.5: "要求 inbox 内没有 open 状态的组，否则先提示").
- For each selected file: target path `<target.path>/<directory>/<name>`; if the target path or a row for it exists, record a conflict and leave the file; else `move_file` (Task 11) and update the row's `folder_id` (id, sha1, pairs and judgments kept); mark moved files dirty and enqueue `regroup` once. Afterwards remove emptied source directories. Progress every 100 files.
- Registered in `IO_HANDLERS["archive_move"]`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_archive_move.py`:

```python
import hashlib

import pytest

from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.archive_move import run_archive_move
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    inbox, arch = tmp_path / "inbox", tmp_path / "arch"
    (inbox / "set1").mkdir(parents=True)
    (inbox / "set2").mkdir()
    arch.mkdir()
    src = folders.create_folder(conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all")
    dst = folders.create_folder(conn, name="Arch", path=str(arch), role="archive", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder_id, root, rel, content):
        directory, _, name = rel.rpartition("/")
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        fid = files.insert_file(conn, folder_id=folder_id, directory=directory, name=name, extension_id=ext, size=len(content), mtime=1)
        files.set_fingerprint_result(conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted")
        return fid

    yield reg, conn, src, dst, inbox, arch, add
    reg.close()


def ctx_for(reg, params):
    conn = reg.conn("a")
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path, settings=settings_db.all(conn), params=params, report=lambda p: None)


def test_moves_selected_directories_keeping_ids_and_pairs(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    b = add(src, inbox, "set2/b.jpg", b"b")
    kept = add(dst, arch, "old/k.jpg", b"a")
    pairs.upsert_sha1_pair(conn, a, kept)
    result = run_archive_move(ctx_for(reg, {"source_folder_id": id_to_str(src), "target_folder_id": id_to_str(dst), "directories": ["set1"]}))
    assert result == {"moved": 1, "conflicts": []}
    assert (arch / "set1" / "a.jpg").read_bytes() == b"a" and not (inbox / "set1").exists()
    row = files.get_file(conn, a)
    assert row["folder_id"] == dst and row["directory"] == "set1" and row["sha1"] == hashlib.sha1(b"a").digest()
    assert len(pairs.pairs_of_file(conn, a)) == 1
    assert files.get_file(conn, b)["folder_id"] == src
    assert set(groups.pop_dirty(conn)) == {a}
    assert jobs_db.has_active(conn, "regroup")
    assert IO_HANDLERS["archive_move"] is run_archive_move


def test_conflicts_are_reported_and_left_in_place(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    add(dst, arch, "set1/a.jpg", b"other")
    result = run_archive_move(ctx_for(reg, {"source_folder_id": id_to_str(src), "target_folder_id": id_to_str(dst)}))
    assert result["moved"] == 0 and result["conflicts"] == ["set1/a.jpg"]
    assert (inbox / "set1" / "a.jpg").exists() and files.get_file(conn, a)["folder_id"] == src


def test_refuses_when_open_groups_remain_or_roles_are_wrong(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    k = add(dst, arch, "k.jpg", b"a")
    pairs.upsert_sha1_pair(conn, a, k)
    groups.replace_groups(conn, [], [NewGroup(frozenset({a, k}), 0.0, True, "open", {})])
    result = run_archive_move(ctx_for(reg, {"source_folder_id": id_to_str(src), "target_folder_id": id_to_str(dst)}))
    assert result["skipped"].startswith("1 open group")
    assert (inbox / "set1" / "a.jpg").exists()
    swapped = run_archive_move(ctx_for(reg, {"source_folder_id": id_to_str(dst), "target_folder_id": id_to_str(src)}))
    assert "inbox" in swapped["skipped"]
```

Run: `uv run pytest tests/integration/test_archive_move.py -q` — Expected: FAIL, module missing.

- [ ] **Step 2: Implement**

`visualstore/jobs/io/archive_move.py`:

```python
"""Archive move (design §8.5): move an inbox Folder's files into an archive Folder, keeping
relative paths, ids, hashes, pairs and judgments."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.domain.paths import is_under, normalise_relative
from visualstore.ids import id_from_str
from visualstore.jobs.io.actions import move_file, remove_empty_parents
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX


def _selected(row, prefixes: list[str]) -> bool:
    return not prefixes or any(is_under(row["directory"], p, recursive=True) for p in prefixes)


def run_archive_move(ctx: IoContext) -> dict:
    prefixes = [normalise_relative(d) for d in ctx.params.get("directories") or []]
    with closing(ctx.open_metadata()) as conn:
        source = folders_db.get_folder(conn, id_from_str(ctx.params["source_folder_id"]))
        target = folders_db.get_folder(conn, id_from_str(ctx.params["target_folder_id"]))
        if source is None or target is None:
            return {"skipped": "folder missing"}
        if source["role"] != "inbox" or target["role"] != "archive":
            return {"skipped": "source must be an inbox Folder and target an archive Folder"}
        rows = [r for r in files_db.list_files_in_folder(conn, bytes(source["id"])) if _selected(r, prefixes)]
        open_groups = {
            bytes(g["id"]) for r in rows
            if (g := groups_db.group_of_file(conn, bytes(r["id"]))) is not None and g["status"] == "open"
        }
        if open_groups:
            return {"skipped": f"{len(open_groups)} open group(s) still contain files from this selection; resolve them first"}

        moved, conflicts, moved_ids = 0, [], []
        for i, row in enumerate(rows, start=1):
            rel = files_db.relative_path(row["directory"], row["name"])
            src = Path(source["path"]) / rel
            dest = Path(target["path"]) / rel
            if dest.exists() or files_db.find_file(conn, bytes(target["id"]), row["directory"], row["name"]):
                conflicts.append(rel)
                continue
            if not src.is_file():
                conflicts.append(rel)
                continue
            move_file(src, dest)
            with conn:
                conn.execute("UPDATE file SET folder_id = ? WHERE id = ?", (bytes(target["id"]), bytes(row["id"])))
            moved += 1
            moved_ids.append(bytes(row["id"]))
            remove_empty_parents(src.parent, Path(source["path"]))
            if i % 100 == 0:
                ctx.report({"done": i, "total": len(rows), "message": rel})
        if moved_ids:
            groups_db.mark_dirty(conn, moved_ids)
            if not jobs_db.has_active(conn, "regroup"):
                jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
        ctx.report({"done": len(rows), "total": len(rows), "message": f"moved {moved}, {len(conflicts)} conflicts"})
        return {"moved": moved, "conflicts": conflicts}
```

Register `"archive_move": run_archive_move` in `IO_HANDLERS`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_archive_move.py -q` — Expected: 3 passed.

```bash
git add visualstore/jobs/io/archive_move.py visualstore/jobs/io/__init__.py tests/integration/test_archive_move.py
git commit -m "feat: archive move job

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: `import_v1` io job

**Files:**
- Create: `visualstore/jobs/io/import_v1.py`, `tests/fixtures/__init__.py`, `tests/fixtures/v1_schema.sql`, `tests/fixtures/v1db.py`, `tests/integration/test_import_v1.py`
- Modify: `visualstore/db/folders.py` (`create_folder(..., folder_id=None)`, `upsert_extension(..., extension_id=None)`), `visualstore/domain/scan.py` + `tests/unit/test_domain_scan.py` (unknown-mtime rule), `visualstore/jobs/io/scan.py` (apply `touch`), `visualstore/jobs/io/__init__.py`

**Interfaces:**
- Job `import_v1`, params `{"db_path": "<container path to the v1 .db>", "folder_paths": {"<v1 Folder.Name>": "<container path>"}}`. `run_import_v1(ctx) -> dict` → `{"folders", "extensions_added", "files", "sha1_pairs", "related_keep", "similar_skipped", "similar_orphans", "scan_jobs"}`. Raises `ImportError_` (name chosen to avoid the builtin) **before writing anything** when the profile already has Folders, the db file is unreadable, or a v1 Folder has no path mapping / the mapped path does not exist.
- v1 reading rules ([08 Appendix A](../design/08-v1-import.md#appendix-a-v1-database-schema-and-import-pitfalls)): ids via `uuid.UUID(bytes_le=blob)`; `Folder.CompareImageWith` 0/1/2 → `all/other_directories/other_folders`; `IsSealed` 1 → `archive`; `Extension.IsImage` 1 → `image`, 0 → `video` if the name is a known video extension else `other`; `Extension.Ignored` 1 → disabled; `File.Path` backslashes → `/`; `File.FileName + '.' + Extension.Extension` → `file.name`; `FileSize` (-1 → 0); `mtime = 0` (unknown); `Sha1Hash` → `sha1` with `state = fingerprinted`; `FileState` 2 (NotReadable) → `unreadable`; everything else `new`. SameFile rows contribute only `IsIgnored`: pairs touching an ignored file get `judgment = related_keep`, `judgment_origin = import_v1`. SimilarFile is counted, not imported; rows referencing missing files are counted as orphans.
- **Unknown-mtime rule** in `domain.scan.diff`: a db file with `mtime == 0` and equal size is unchanged, and goes into `plan.touch: list[tuple[bytes, DiskEntry]]` so the scan records the real mtime without resetting the hash.
- Ends by enqueuing one `scan` per imported Folder at `PRIORITY_USER` and a `regroup`.
- Registered in `IO_HANDLERS["import_v1"]`.

- [ ] **Step 1: Write the fixture, then the failing tests**

`tests/fixtures/__init__.py`: empty.

`tests/fixtures/v1_schema.sql` (from design Appendix A.1):

```sql
CREATE TABLE Folder (Id BLOB PRIMARY KEY, Name TEXT COLLATE NOCASE UNIQUE, Path TEXT COLLATE NOCASE,
                     CompareImageWith INTEGER, IsSealed INTEGER);
CREATE TABLE Extension (Id BLOB PRIMARY KEY, Extension TEXT COLLATE NOCASE UNIQUE, IsImage INTEGER, Ignored INTEGER);
CREATE TABLE File (Id BLOB PRIMARY KEY, FolderId BLOB REFERENCES Folder(Id) ON DELETE CASCADE,
                   Path TEXT COLLATE NOCASE, FileName TEXT COLLATE NOCASE,
                   ExtensionId BLOB REFERENCES Extension(Id), ImageHash BLOB, Sha1Hash BLOB,
                   FileSize INTEGER DEFAULT -1, FileState INTEGER, ImageComparedThreshold REAL);
CREATE TABLE IgnoredDirectory (Id BLOB PRIMARY KEY, FolderId BLOB REFERENCES Folder(Id) ON DELETE CASCADE,
                               Directory TEXT COLLATE NOCASE, IsSubDirectoryIncluded INTEGER);
CREATE TABLE SameFile (Id BLOB PRIMARY KEY, Sha1Hash BLOB, FileId BLOB UNIQUE REFERENCES File(Id) ON DELETE CASCADE,
                       IsIgnored INTEGER);
CREATE TABLE SimilarFile (Id BLOB PRIMARY KEY, File1Id BLOB, File2Id BLOB, DifferenceDegree REAL, IgnoredMode INTEGER);
```

`tests/fixtures/v1db.py`:

```python
"""Build a tiny ImageStore v1 database the way .NET would have written it (mixed-endian Guids)."""

import sqlite3
import uuid
from pathlib import Path

SCHEMA = (Path(__file__).parent / "v1_schema.sql").read_text()


def guid() -> uuid.UUID:
    return uuid.uuid4()


def le(u: uuid.UUID) -> bytes:
    return u.bytes_le


def build(path: Path) -> dict:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    archive, inbox = guid(), guid()
    jpg, png, mp4, zip_ = guid(), guid(), guid(), guid()
    f_a, f_b, f_c, f_d, f_gone = guid(), guid(), guid(), guid(), guid()
    sha_dup, sha_other = b"\x11" * 20, b"\x22" * 20
    conn.executemany(
        "INSERT INTO Folder VALUES (?, ?, ?, ?, ?)",
        [(le(archive), "Archive", r"D:\Images", 1, 1), (le(inbox), "Inbox", r"E:\New", 0, 0)],
    )
    conn.executemany(
        "INSERT INTO Extension VALUES (?, ?, ?, ?)",
        [(le(jpg), "jpg", 1, 0), (le(png), "PNG", 1, 0), (le(mp4), "mp4", 0, 0), (le(zip_), "zip", 0, 1)],
    )
    conn.executemany(
        "INSERT INTO File VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (le(f_a), le(archive), r"Set A\Sub", "a", le(jpg), None, sha_dup, 100, 255, 0.5),
            (le(f_b), le(inbox), "", "b", le(jpg), None, sha_dup, 100, 255, None),
            (le(f_c), le(inbox), "", "c", le(png), None, sha_other, 7, 255, None),
            (le(f_d), le(inbox), "clips", "d", le(mp4), None, None, -1, 2, None),
        ],
    )
    conn.execute("INSERT INTO IgnoredDirectory VALUES (?, ?, ?, ?)", (le(guid()), le(archive), r"Thumbs", 1))
    conn.executemany(
        "INSERT INTO SameFile VALUES (?, ?, ?, ?)",
        [(le(guid()), sha_dup, le(f_a), 0), (le(guid()), sha_dup, le(f_b), 1)],
    )
    conn.executemany(
        "INSERT INTO SimilarFile VALUES (?, ?, ?, ?, ?)",
        [(le(guid()), le(f_a), le(f_c), 0.01, 1), (le(guid()), le(f_c), le(f_gone), 0.02, 0)],
    )
    conn.commit()
    conn.close()
    return {"archive": archive, "inbox": inbox, "jpg": jpg, "mp4": mp4, "zip": zip_, "f_a": f_a, "f_b": f_b, "f_c": f_c, "f_d": f_d}
```

`tests/integration/test_import_v1.py`:

```python
import pytest

from tests.fixtures.v1db import build
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.import_v1 import ImportError_, run_import_v1
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    (tmp_path / "media" / "images").mkdir(parents=True)
    (tmp_path / "media" / "new").mkdir()
    ids = build(tmp_path / "v1.db")
    yield reg, reg.conn("a"), tmp_path, ids
    reg.close()


def ctx_for(reg, params):
    conn = reg.conn("a")
    return IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path, settings=settings_db.all(conn), params=params, report=lambda p: None)


def params_for(tmp_path, **extra):
    base = {"db_path": str(tmp_path / "v1.db"), "folder_paths": {"Archive": str(tmp_path / "media" / "images"), "Inbox": str(tmp_path / "media" / "new")}}
    base.update(extra)
    return base


def test_import_preserves_ids_and_maps_everything(world):
    """Review Focus 5."""
    reg, conn, tmp_path, ids = world
    result = run_import_v1(ctx_for(reg, params_for(tmp_path)))
    assert result["folders"] == 2 and result["files"] == 4 and result["sha1_pairs"] == 1
    assert result["related_keep"] == 1 and result["similar_skipped"] == 2 and result["similar_orphans"] == 1
    assert result["scan_jobs"] == 2 and result["extensions_added"] >= 1  # zip was not a default

    by_name = {r["name"]: r for r in folders.list_folders(conn)}
    assert id_to_str(by_name["Archive"]["id"]) == str(ids["archive"])  # bytes_le read correctly
    assert by_name["Archive"]["role"] == "archive" and by_name["Archive"]["compare_scope"] == "other_directories"
    assert by_name["Inbox"]["role"] == "inbox" and by_name["Inbox"]["path"] == str(tmp_path / "media" / "new")
    [ign] = folders.list_ignored(conn, by_name["Archive"]["id"])
    assert ign["directory"] == "Thumbs" and ign["recursive"] == 1

    ext = {r["extension"]: r for r in folders.list_extensions(conn)}
    assert ext["mp4"]["media_type"] == "video" and ext["zip"]["media_type"] == "other" and ext["zip"]["enabled"] == 0
    assert id_to_str(ext["zip"]["id"]) == str(ids["zip"])
    assert id_to_str(ext["jpg"]["id"]) != str(ids["jpg"])  # default row kept, v1 id not needed

    a = files.get_file(conn, ids["f_a"].bytes)
    assert a["directory"] == "Set A/Sub" and a["name"] == "a.jpg" and a["size"] == 100 and a["mtime"] == 0
    assert a["state"] == "fingerprinted" and a["sha1"] == b"\x11" * 20
    d = files.get_file(conn, ids["f_d"].bytes)
    assert d["name"] == "d.mp4" and d["state"] == "unreadable" and d["size"] == 0

    [p] = pairs.pairs_of_file(conn, ids["f_a"].bytes)
    assert p["source"] == "sha1" and p["judgment"] == "related_keep" and p["judgment_origin"] == "import_v1"
    assert groups.dirty_count(conn) == 2
    queued = jobs_db.list_jobs(conn, state="queued")
    assert sorted(j["type"] for j in queued) == ["regroup", "scan", "scan"]
    assert all(j["priority"] == 100 for j in queued if j["type"] == "scan")
    assert IO_HANDLERS["import_v1"] is run_import_v1


def test_refuses_non_empty_profile_and_missing_mapping_without_writing(world):
    reg, conn, tmp_path, ids = world
    with pytest.raises(ImportError_, match="Inbox"):
        run_import_v1(ctx_for(reg, params_for(tmp_path, folder_paths={"Archive": str(tmp_path / "media" / "images")})))
    assert folders.list_folders(conn) == []
    with pytest.raises(ImportError_, match="exist"):
        run_import_v1(ctx_for(reg, params_for(tmp_path, folder_paths={"Archive": str(tmp_path / "media" / "images"), "Inbox": str(tmp_path / "nope")})))
    assert folders.list_folders(conn) == []
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    with pytest.raises(ImportError_, match="empty"):
        run_import_v1(ctx_for(reg, params_for(tmp_path)))
    with pytest.raises(ImportError_):
        run_import_v1(ctx_for(reg, params_for(tmp_path, db_path=str(tmp_path / "missing.db"))))


def test_scan_after_import_keeps_hashes_when_only_mtime_was_unknown(world):
    reg, conn, tmp_path, ids = world
    (tmp_path / "media" / "new" / "b.jpg").write_bytes(b"x" * 100)
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    from visualstore.jobs.io.scan import run_scan

    inbox = next(r for r in folders.list_folders(conn) if r["name"] == "Inbox")
    result = run_scan(ctx_for(reg, {"folder_id": id_to_str(inbox["id"])}))
    b = files.get_file(conn, ids["f_b"].bytes)
    assert b["sha1"] == b"\x11" * 20 and b["state"] == "fingerprinted" and b["mtime"] > 0
    assert result["changed"] == 0 and result["deleted"] == 2  # c.png and clips/d.mp4 are not on disk
```

Add to `tests/unit/test_domain_scan.py`:

```python
def test_unknown_mtime_with_same_size_is_a_touch_not_a_change():
    plan = diff(disk=[entry("x.jpg", size=5, mtime=99)], db=[dbfile(b"1" * 16, "x.jpg", size=5, mtime=0)])
    assert plan.changed == [] and plan.unchanged == 1
    assert [(fid, e.mtime) for fid, e in plan.touch] == [(b"1" * 16, 99)]
    plan2 = diff(disk=[entry("x.jpg", size=6, mtime=99)], db=[dbfile(b"1" * 16, "x.jpg", size=5, mtime=0)])
    assert len(plan2.changed) == 1 and plan2.touch == []
```

Run: `uv run pytest tests/integration/test_import_v1.py tests/unit/test_domain_scan.py -q` — Expected: FAIL (module missing; `touch` missing).

- [ ] **Step 2: Implement the small modifications**

`visualstore/domain/scan.py`: add `touch: list[tuple[bytes, DiskEntry]] = field(default_factory=list)` to `ScanPlan`, and in `diff` replace the unchanged/changed branch with:

```python
        elif existing.size == entry.size and (existing.mtime == entry.mtime or existing.mtime == 0):
            plan.unchanged += 1
            if existing.mtime == 0:
                plan.touch.append((existing.id, entry))
        else:
            plan.changed.append((existing.id, entry))
```

`visualstore/jobs/io/scan.py`: after the `plan.changed` loop add `for fid, e in plan.touch: files_db.update_stat(conn, fid, e.size, e.mtime)`.

`visualstore/db/folders.py`: `create_folder(..., priority=0, folder_id: bytes | None = None)` uses `folder_id or new_id()`; `upsert_extension(..., enabled=True, extension_id: bytes | None = None)` uses `extension_id or new_id()` on insert.

- [ ] **Step 3: Implement the import job**

`visualstore/jobs/io/import_v1.py`:

```python
"""One-time import of an ImageStore v1 database (design §10, Appendix A). Read-only on v1."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.domain.paths import normalise_relative
from visualstore.ids import id_to_str
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_INBOX, PRIORITY_USER

SCOPE = {0: "all", 1: "other_directories", 2: "other_folders"}
VIDEO = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "wmv", "flv"}
STATE_NOT_READABLE = 2


class ImportError_(Exception):
    pass


def gid(blob: bytes) -> bytes:
    """v1 ids were written by .NET Guid.ToByteArray(): mixed-endian. Read with bytes_le."""
    return uuid.UUID(bytes_le=bytes(blob)).bytes


def _open_v1(path: str) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise ImportError_(f"v1 database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM Folder LIMIT 1")
    except sqlite3.Error as exc:
        raise ImportError_(f"not a readable v1 database: {exc}") from exc
    return conn


def run_import_v1(ctx: IoContext) -> dict:
    mapping: dict[str, str] = ctx.params.get("folder_paths") or {}
    with closing(ctx.open_metadata()) as conn, closing(_open_v1(ctx.params["db_path"])) as v1:
        if folders_db.list_folders(conn):
            raise ImportError_("The profile must be empty (no Folders) before importing a v1 database.")
        v1_folders = v1.execute("SELECT * FROM Folder").fetchall()
        missing = [f["Name"] for f in v1_folders if f["Name"] not in mapping]
        if missing:
            raise ImportError_(f"No container path given for v1 Folder(s): {', '.join(missing)}")
        for name in mapping:
            if not Path(mapping[name]).is_dir():
                raise ImportError_(f"Mapped path for '{name}' does not exist in the container: {mapping[name]}")

        stats = dict.fromkeys(
            ["folders", "extensions_added", "files", "sha1_pairs", "related_keep", "similar_skipped", "similar_orphans", "scan_jobs"], 0
        )
        # folders + ignored directories
        folder_ids: dict[bytes, bytes] = {}
        for f in v1_folders:
            fid = folders_db.create_folder(
                conn, name=f["Name"], path=mapping[f["Name"]],
                role="archive" if f["IsSealed"] else "inbox", compare_scope=SCOPE.get(f["CompareImageWith"], "all"),
                folder_id=gid(f["Id"]),
            )
            folder_ids[bytes(f["Id"])] = fid
            stats["folders"] += 1
        for d in v1.execute("SELECT * FROM IgnoredDirectory"):
            folder = folder_ids.get(bytes(d["FolderId"]))
            if folder is not None and normalise_relative(d["Directory"]):
                try:
                    folders_db.add_ignored(conn, folder, d["Directory"], bool(d["IsSubDirectoryIncluded"]))
                except folders_db.FolderError:
                    pass  # duplicate after normalisation
        # extensions
        existing = {r["extension"].lower(): bytes(r["id"]) for r in folders_db.list_extensions(conn)}
        ext_ids: dict[bytes, bytes] = {}
        ext_names: dict[bytes, str] = {}
        for e in v1.execute("SELECT * FROM Extension"):
            name = e["Extension"].lower().lstrip(".")
            ext_names[bytes(e["Id"])] = name
            if name in existing:
                ext_ids[bytes(e["Id"])] = existing[name]
                if e["Ignored"]:
                    folders_db.set_extension_enabled(conn, existing[name], False)
                continue
            media = "image" if e["IsImage"] else ("video" if name in VIDEO else "other")
            ext_ids[bytes(e["Id"])] = folders_db.upsert_extension(conn, name, media, enabled=not e["Ignored"], extension_id=gid(e["Id"]))
            stats["extensions_added"] += 1
        # files
        ignored_files = {bytes(r["FileId"]) for r in v1.execute("SELECT FileId FROM SameFile WHERE IsIgnored = 1")}
        file_ids: dict[bytes, bytes] = {}
        for f in v1.execute("SELECT * FROM File"):
            folder = folder_ids.get(bytes(f["FolderId"]))
            ext_id = ext_ids.get(bytes(f["ExtensionId"]))
            if folder is None or ext_id is None:
                continue
            sha1 = bytes(f["Sha1Hash"]) if f["Sha1Hash"] else None
            state = "unreadable" if f["FileState"] == STATE_NOT_READABLE else ("fingerprinted" if sha1 else "new")
            new_id_ = files_db.insert_file(
                conn, folder_id=folder, directory=normalise_relative(f["Path"] or ""),
                name=f"{f['FileName']}.{ext_names[bytes(f['ExtensionId'])]}", extension_id=ext_id,
                size=max(0, int(f["FileSize"] or 0)), mtime=0, state=state, sha1=sha1, file_id=gid(f["Id"]),
            )
            file_ids[bytes(f["Id"])] = new_id_
            stats["files"] += 1
            if stats["files"] % 1000 == 0:
                ctx.report({"done": stats["files"], "total": 0, "message": f"{stats['files']} files"})
        # byte-identical pairs from sha1 buckets (not from SameFile, which may be incomplete)
        ignored_v2 = {file_ids[v] for v in ignored_files if v in file_ids}
        dirty: list[bytes] = []
        buckets = conn.execute(
            "SELECT sha1 FROM file WHERE sha1 IS NOT NULL GROUP BY sha1 HAVING COUNT(*) > 1"
        ).fetchall()
        for b in buckets:
            members = [bytes(r["id"]) for r in files_db.files_by_sha1(conn, bytes(b["sha1"]))]
            hub = members[0]
            for other in members[1:]:
                pairs_db.upsert_sha1_pair(conn, hub, other)
                stats["sha1_pairs"] += 1
                if hub in ignored_v2 or other in ignored_v2:
                    pairs_db.set_judgment(conn, hub, other, "related_keep", "import_v1")
                    stats["related_keep"] += 1
            dirty.extend(members)
        groups_db.mark_dirty(conn, dirty)
        # similar files: counted only
        for s in v1.execute("SELECT File1Id, File2Id FROM SimilarFile"):
            stats["similar_skipped"] += 1
            if bytes(s["File1Id"]) not in file_ids or bytes(s["File2Id"]) not in file_ids:
                stats["similar_orphans"] += 1
        # follow-up jobs
        for fid in folder_ids.values():
            jobs_db.enqueue(conn, type="scan", kind="io", priority=PRIORITY_USER, params={"folder_id": id_to_str(fid), "priority": PRIORITY_USER})
            stats["scan_jobs"] += 1
        if dirty and not jobs_db.has_active(conn, "regroup"):
            jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
        return stats
```

Register `"import_v1": run_import_v1` in `IO_HANDLERS`.

- [ ] **Step 4: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_import_v1.py tests/integration/test_scan_job.py tests/unit/test_domain_scan.py tests/unit/test_db_folders.py -q` — Expected: all passed.

```bash
git add visualstore/jobs/io/import_v1.py visualstore/jobs/io/__init__.py visualstore/db/folders.py visualstore/domain/scan.py visualstore/jobs/io/scan.py tests/fixtures tests/integration/test_import_v1.py tests/unit/test_domain_scan.py
git commit -m "feat: v1 database import with .NET guid handling and sha1-bucket pairs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Milestone 2 settings and the scan / purge planners

**Files:**
- Modify: `visualstore/db/settings.py`, `visualstore/db/jobs.py`, `visualstore/app.py`
- Create: `visualstore/jobs/io/planners.py`, `tests/unit/test_planners.py`
- Modify: `tests/unit/test_settings_db.py`

**Interfaces:**
- New defaults in `settings.DEFAULTS`: `"trash.enabled": True`, `"trash.retention_days": 30`, `"resolution.auto_execute_identical": True`, `"scan.interval_hours": 24` (0 disables scheduled scans), `"grouping.group_distance": 0.2` (float; milestone 3 tunes it). Range rule: integers and floats must be `>= 0`, `trash.retention_days` and `backup.*.keep` `>= 1` (closes the deferred minor from milestone 1: `keep = 0` deleted the backup just written) — `settings.set` raises `ValueError` otherwise.
- `jobs_db.latest_with_param(conn, type, key, value) -> Row | None` and `jobs_db.has_active_with_param(conn, type, key, value) -> bool` using `json_extract(params, '$.<key>')`.
- `planners.plan_scans(registry, now=None) -> int`: for each profile and enabled Folder, when `scan.interval_hours > 0`, no scan for that Folder is queued or running, and the latest scan for it either never ran or finished more than `interval` ago: enqueue `scan` with the Folder's role priority. `planners.plan_purge(registry, now=None) -> int`: once a day per profile when `trash.enabled`. Both registered in `app.py` alongside `plan_backups`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_settings_db.py`:

```python
def test_milestone_2_defaults_and_ranges(conn):
    assert settings.get(conn, "trash.enabled") is True
    assert settings.get(conn, "trash.retention_days") == 30
    assert settings.get(conn, "resolution.auto_execute_identical") is True
    assert settings.get(conn, "scan.interval_hours") == 24
    assert settings.get(conn, "grouping.group_distance") == 0.2
    settings.set(conn, "scan.interval_hours", 0)  # 0 = disabled is allowed
    with pytest.raises(ValueError):
        settings.set(conn, "trash.retention_days", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "backup.metadata.keep", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "grouping.group_distance", -0.1)
    settings.set(conn, "grouping.group_distance", 0.35)
    assert settings.get(conn, "grouping.group_distance") == 0.35
```

`tests/unit/test_planners.py`:

```python
import json
from datetime import UTC, datetime, timedelta

import pytest

from visualstore.db import folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.planners import plan_purge, plan_scans
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    (tmp_path / "arch").mkdir()
    (tmp_path / "inbox").mkdir()
    conn = reg.conn("a")
    folders.create_folder(conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all")
    folders.create_folder(conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all")
    yield reg
    reg.close()


def test_plan_scans_once_per_folder_with_role_priority(registry):
    conn = registry.conn("a")
    assert plan_scans(registry) == 2
    assert plan_scans(registry) == 0  # still queued
    by_folder = {json.loads(j["params"])["folder_id"]: j for j in jobs_db.list_jobs(conn)}
    roles = {id_to_str(f["id"]): f["role"] for f in folders.list_folders(conn)}
    for fid, job in by_folder.items():
        assert job["priority"] == (50 if roles[fid] == "inbox" else 20)


def test_plan_scans_respects_interval_disabled_folders_and_setting_zero(registry):
    conn = registry.conn("a")
    plan_scans(registry)
    for job in jobs_db.list_jobs(conn):
        jobs_db.mark_running(conn, job["id"])
        jobs_db.mark_done(conn, job["id"])
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=23)) == 0
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=25)) == 2
    for job in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, job["id"])
    arch = next(f for f in folders.list_folders(conn) if f["role"] == "archive")
    folders.update_folder(conn, bytes(arch["id"]), enabled=False)
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=50)) == 1
    settings_db.set(conn, "scan.interval_hours", 0)
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=100)) == 0


def test_plan_purge_daily_when_trash_enabled(registry):
    conn = registry.conn("a")
    assert plan_purge(registry) == 1
    assert plan_purge(registry) == 0
    [job] = jobs_db.list_jobs(conn)
    assert job["type"] == "purge_trash" and job["priority"] == 0
    jobs_db.mark_running(conn, job["id"])
    jobs_db.mark_done(conn, job["id"])
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=12)) == 0
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=25)) == 1
    settings_db.set(conn, "trash.enabled", False)
    for j in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, j["id"])
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=100)) == 0
```

Run: `uv run pytest tests/unit/test_settings_db.py tests/unit/test_planners.py -q` — Expected: FAIL.

- [ ] **Step 2: Implement**

`visualstore/db/settings.py`: extend `DEFAULTS` with the five keys above and add range checks in `set` after the type check:

```python
    if isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        minimum = 1 if key in _AT_LEAST_ONE else 0
        if value < minimum:
            raise ValueError(f"setting {key!r} must be >= {minimum}")
```

with `_AT_LEAST_ONE = {"backup.metadata.keep", "backup.vectors.keep", "trash.retention_days"}` (note `bool` is checked first because `bool` is an `int` subclass — but the type check already rejects bools for int settings; this guard keeps the bool settings themselves out of the range check).

`visualstore/db/jobs.py`:

```python
def latest_with_param(conn: sqlite3.Connection, type: str, key: str, value) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM job WHERE type = ? AND json_extract(params, '$.{key}') = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (type, value),
    ).fetchone()


def has_active_with_param(conn: sqlite3.Connection, type: str, key: str, value) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM job WHERE type = ? AND state IN ('queued', 'running')"
        f" AND json_extract(params, '$.{key}') = ? LIMIT 1",
        (type, value),
    ).fetchone()
    return row is not None
```

(`key` is always a literal from our own code, never user input.)

`visualstore/jobs/io/planners.py`:

```python
"""Time-based planners for milestone 2 jobs: scheduled scans and trash purge (design §7.1, §8.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from visualstore.clock import parse_iso
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX, PRIORITY_MAINTENANCE
from visualstore.profiles import ProfileRegistry


def _due(last, interval: timedelta, now: datetime) -> bool:
    if last is None or not last["finished_at"]:
        return last is None
    return now - parse_iso(last["finished_at"]) >= interval


def plan_scans(registry: ProfileRegistry, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        hours = int(settings_db.get(conn, "scan.interval_hours"))
        if hours <= 0:
            continue
        for folder in folders_db.list_folders(conn):
            if not folder["enabled"]:
                continue
            fid = id_to_str(bytes(folder["id"]))
            if jobs_db.has_active_with_param(conn, "scan", "folder_id", fid):
                continue
            last = jobs_db.latest_with_param(conn, "scan", "folder_id", fid)
            if not _due(last, timedelta(hours=hours), now):
                continue
            jobs_db.enqueue(
                conn, type="scan", kind="io", params={"folder_id": fid},
                priority=PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE,
            )
            enqueued += 1
    return enqueued


def plan_purge(registry: ProfileRegistry, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if not settings_db.get(conn, "trash.enabled") or jobs_db.has_active(conn, "purge_trash"):
            continue
        if not _due(jobs_db.latest_of_type(conn, "purge_trash"), timedelta(hours=24), now):
            continue
        jobs_db.enqueue(conn, type="purge_trash", kind="io", priority=PRIORITY_MAINTENANCE)
        enqueued += 1
    return enqueued
```

`visualstore/app.py`: `planners=[partial(plan_backups, backup_dir=config.backup_dir), plan_scans, plan_purge]` (import both from `visualstore.jobs.io.planners`).

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/unit -q` — Expected: all passed.

```bash
git add visualstore/db/settings.py visualstore/db/jobs.py visualstore/jobs/io/planners.py visualstore/app.py tests/unit/test_settings_db.py tests/unit/test_planners.py
git commit -m "feat: milestone 2 settings with range checks; scheduled scan and purge planners

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Folders, ignored directories, extensions and scan-trigger API

**Files:**
- Create: `visualstore/api/folders.py`, `visualstore/api/ids.py`, `tests/integration/test_api_folders.py`
- Modify: `visualstore/app.py` (router + `FolderError` → 400 handler)

**Interfaces:**
- `visualstore.api.ids.parse_id(text) -> bytes` raising `HTTPException(404)` for malformed ids.
- Routes under `/api/p/{name}` (auth + `get_profile`), all `async def`:
  - `GET /folders` → `[FolderOut]` where `FolderOut = folder_to_dict + {"ignored": [{"id", "directory", "recursive"}]}`.
  - `POST /folders {name, path, role, compare_scope, priority?}` → 201 `FolderOut`.
  - `PATCH /folders/{folder_id} {name?, role?, compare_scope?, enabled?, priority?}` → `FolderOut`.
  - `DELETE /folders/{folder_id}` → `{ok}` (removes rows only; files on disk untouched).
  - `POST /folders/{folder_id}/ignored {directory, recursive?}` → 201 `FolderOut`; `DELETE /folders/{folder_id}/ignored/{ignored_id}` → `FolderOut`.
  - `POST /folders/{folder_id}/scan` → 201 job (priority 100, params `{"folder_id", "priority": 100}`); 409 when a scan for that Folder is already queued or running.
  - `GET /extensions` → `[{id, extension, media_type, enabled}]`; `PUT /extensions {extension, media_type, enabled?}` → list; `PATCH /extensions/{extension_id} {enabled}` → list.
- `FolderError` is mapped to 400 by an exception handler in `app.py`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_api_folders.py`:

```python
import pytest

from tests.integration.conftest import login


@pytest.fixture
def media(tmp_path):
    for d in ("images", "images/sub", "new"):
        (tmp_path / "media" / d).mkdir(parents=True)
    return tmp_path / "media"


def setup(client):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201


def test_folder_crud_ignored_and_validation(client, media):
    """Review Focus 1 at the HTTP boundary."""
    setup(client)
    created = client.post("/api/p/a/folders", json={"name": "Images", "path": str(media / "images"), "role": "archive", "compare_scope": "other_directories"})
    assert created.status_code == 201, created.text
    folder = created.json()
    assert folder["role"] == "archive" and folder["ignored"] == [] and folder["enabled"] is True

    nested = client.post("/api/p/a/folders", json={"name": "Sub", "path": str(media / "images" / "sub"), "role": "inbox", "compare_scope": "all"})
    assert nested.status_code == 400 and "nest" in nested.json()["detail"]
    assert client.post("/api/p/a/folders", json={"name": "Rel", "path": "relative", "role": "inbox", "compare_scope": "all"}).status_code == 400
    assert client.post("/api/p/a/folders", json={"name": "Bad", "path": str(media / "new"), "role": "sealed", "compare_scope": "all"}).status_code == 400

    with_ignored = client.post(f"/api/p/a/folders/{folder['id']}/ignored", json={"directory": "Thumbs/", "recursive": False})
    assert with_ignored.status_code == 201
    [ign] = with_ignored.json()["ignored"]
    assert ign["directory"] == "Thumbs" and ign["recursive"] is False
    assert client.delete(f"/api/p/a/folders/{folder['id']}/ignored/{ign['id']}").json()["ignored"] == []

    patched = client.patch(f"/api/p/a/folders/{folder['id']}", json={"enabled": False, "priority": 3, "name": "Images 2"})
    assert patched.status_code == 200 and patched.json()["enabled"] is False and patched.json()["priority"] == 3
    assert client.patch(f"/api/p/a/folders/{folder['id']}", json={"path": "/x"}).status_code == 422

    assert [f["name"] for f in client.get("/api/p/a/folders").json()] == ["Images 2"]
    assert client.delete(f"/api/p/a/folders/{folder['id']}").status_code == 200
    assert client.get("/api/p/a/folders").json() == []
    assert client.get("/api/p/a/folders/not-an-id/ignored").status_code in (404, 405)
    assert client.delete("/api/p/a/folders/00000000-0000-0000-0000-000000000000").status_code == 404


def test_scan_trigger_queues_once(client, media):
    setup(client)
    folder = client.post("/api/p/a/folders", json={"name": "New", "path": str(media / "new"), "role": "inbox", "compare_scope": "all"}).json()
    first = client.post(f"/api/p/a/folders/{folder['id']}/scan")
    assert first.status_code == 201
    job = first.json()
    assert job["type"] == "scan" and job["priority"] == 100 and job["params"]["folder_id"] == folder["id"]
    second = client.post(f"/api/p/a/folders/{folder['id']}/scan")
    assert second.status_code in (201, 409)  # 409 while queued; 201 only if it already finished
    if second.status_code == 409:
        assert second.json()["detail"].startswith("A scan")


def test_extensions(client):
    setup(client)
    listed = client.get("/api/p/a/extensions").json()
    jpg = next(e for e in listed if e["extension"] == "jpg")
    assert jpg["media_type"] == "image" and jpg["enabled"] is True
    updated = client.put("/api/p/a/extensions", json={"extension": ".PSD", "media_type": "other"}).json()
    psd = next(e for e in updated if e["extension"] == "psd")
    assert psd["media_type"] == "other"
    toggled = client.patch(f"/api/p/a/extensions/{psd['id']}", json={"enabled": False}).json()
    assert next(e for e in toggled if e["extension"] == "psd")["enabled"] is False
    assert client.put("/api/p/a/extensions", json={"extension": "x", "media_type": "nope"}).status_code == 400
```

Run: `uv run pytest tests/integration/test_api_folders.py -q` — Expected: FAIL (404s, routes missing).

- [ ] **Step 2: Implement**

`visualstore/api/ids.py`:

```python
"""Path-parameter id parsing shared by the profile-scoped routers."""

import uuid

from fastapi import HTTPException


def parse_id(text: str) -> bytes:
    try:
        return uuid.UUID(text).bytes
    except ValueError:
        raise HTTPException(status_code=404, detail="No such item.") from None
```

`visualstore/api/folders.py`:

```python
"""Folder management API (design §8.1 page 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}", tags=["folders"], dependencies=[Depends(require_auth)])


class CreateFolder(BaseModel):
    name: str
    path: str
    role: str
    compare_scope: str
    priority: int = 0


class UpdateFolder(BaseModel, extra="forbid"):
    name: str | None = None
    role: str | None = None
    compare_scope: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class AddIgnored(BaseModel):
    directory: str
    recursive: bool = True


class UpsertExtension(BaseModel):
    extension: str
    media_type: str
    enabled: bool = True


class ToggleExtension(BaseModel):
    enabled: bool


def _folder_out(conn, row) -> dict:
    d = folders_db.folder_to_dict(row)
    d["ignored"] = [
        {"id": id_to_str(bytes(i["id"])), "directory": i["directory"], "recursive": bool(i["recursive"])}
        for i in folders_db.list_ignored(conn, bytes(row["id"]))
    ]
    return d


def _folder_or_404(conn, folder_id: bytes):
    row = folders_db.get_folder(conn, folder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such Folder.")
    return row


@router.get("/folders")
async def list_folders(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    return [_folder_out(conn, r) for r in folders_db.list_folders(conn)]


@router.post("/folders", status_code=201)
async def create_folder(body: CreateFolder, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = folders_db.create_folder(conn, name=body.name, path=body.path, role=body.role, compare_scope=body.compare_scope, priority=body.priority)
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, body: UpdateFolder, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.update_folder(conn, fid, **body.model_dump(exclude_none=True))
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.delete_folder(conn, fid)
    return {"ok": True}


@router.post("/folders/{folder_id}/ignored", status_code=201)
async def add_ignored(folder_id: str, body: AddIgnored, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.add_ignored(conn, fid, body.directory, body.recursive)
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.delete("/folders/{folder_id}/ignored/{ignored_id}")
async def remove_ignored(folder_id: str, ignored_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    folders_db.remove_ignored(conn, parse_id(ignored_id))
    return _folder_out(conn, folders_db.get_folder(conn, fid))


@router.post("/folders/{folder_id}/scan", status_code=201)
async def trigger_scan(folder_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(folder_id)
    _folder_or_404(conn, fid)
    if jobs_db.has_active_with_param(conn, "scan", "folder_id", folder_id):
        raise HTTPException(status_code=409, detail="A scan of this Folder is already queued or running.")
    job_id = jobs_db.enqueue(conn, type="scan", kind="io", priority=PRIORITY_USER, params={"folder_id": folder_id, "priority": PRIORITY_USER})
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


def _extensions(conn) -> list[dict]:
    return [
        {"id": id_to_str(bytes(r["id"])), "extension": r["extension"], "media_type": r["media_type"], "enabled": bool(r["enabled"])}
        for r in folders_db.list_extensions(conn)
    ]


@router.get("/extensions")
async def list_extensions(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    return _extensions(get_state(request).registry.conn(name))


@router.put("/extensions")
async def upsert_extension(body: UpsertExtension, request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    folders_db.upsert_extension(conn, body.extension, body.media_type, body.enabled)
    return _extensions(conn)


@router.patch("/extensions/{extension_id}")
async def toggle_extension(extension_id: str, body: ToggleExtension, request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    folders_db.set_extension_enabled(conn, parse_id(extension_id), body.enabled)
    return _extensions(conn)
```

`visualstore/app.py`: import `folders as folders_api` and `from visualstore.db.folders import FolderError`; add handler

```python
    @app.exception_handler(FolderError)
    async def _folder_error(request: Request, exc: FolderError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
```

and `app.include_router(folders_api.router)`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_api_folders.py tests/integration/test_api_async.py -q` — Expected: all passed.

```bash
git add visualstore/api/folders.py visualstore/api/ids.py visualstore/app.py tests/integration/test_api_folders.py
git commit -m "feat: folders, ignored directories, extensions and scan trigger API

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Groups, pair judgments and files API

**Files:**
- Create: `visualstore/api/groups.py`, `visualstore/api/files.py`, `tests/integration/test_api_groups.py`
- Modify: `visualstore/app.py`

**Interfaces** (all under `/api/p/{name}`, auth, `get_profile`, async):
- `GET /groups?status&folder_id&has_identical&min_size&limit=50&offset=0` → `[GroupOut]`, `GroupOut = group_to_dict + {"members": [member_to_dict]}`; `status`/`has_identical` validated (400).
- `GET /groups/counts` → `count_by_status` + `{"dirty": n}`.
- `GET /groups/{group_id}` → `{"group": GroupOut, "pairs": [pair_to_dict among members]}`.
- `POST /groups/{group_id}/resolve {decisions: {file_id: "keep"|"delete"}}` → every member must be decided (400 otherwise, listing undecided ids); stores decisions; pairs between kept members get `related_keep` (origin `user`); deletes → `apply_action` job `{group_id, trash}` at `PRIORITY_USER` (the job marks the group `resolved`); no deletes → `resolved` immediately. Returns `{"group": GroupOut, "job": JobOut | null}`.
- `POST /groups/{group_id}/dismiss` → all pairs among members `related_keep`, status `dismissed`; returns `GroupOut`.
- `PUT /pairs/{file_a}/{file_b}/judgment {judgment}` → `set_judgment(..., "user")` (404 when no such pair), marks both files dirty, enqueues `regroup` (if not active) at `PRIORITY_USER`; returns `pair_to_dict`.
- `GET /files/{file_id}` → `file_to_dict + {"group_id", "pairs": [pair_to_dict]}`; `GET /files/{file_id}/content` → `FileResponse` of the original (404 when the row or the disk file is missing).

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_api_groups.py`:

```python
import hashlib
import time

import pytest

from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders, groups, pairs
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str


@pytest.fixture
def seeded(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    arch, inbox = tmp_path / "arch", tmp_path / "inbox"
    arch.mkdir(); inbox.mkdir()
    fa = folders.create_folder(conn, name="Arch", path=str(arch), role="archive", compare_scope="all")
    fi = folders.create_folder(conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, root, name, content):
        (root / name).write_bytes(content)
        fid = files.insert_file(conn, folder_id=folder, directory="", name=name, extension_id=ext, size=len(content), mtime=1)
        files.set_fingerprint_result(conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted")
        return fid

    a = add(fa, arch, "a.jpg", b"same")
    b = add(fi, inbox, "b.jpg", b"same")
    c = add(fi, inbox, "c.jpg", b"same")
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.upsert_sha1_pair(conn, a, c)
    [gid] = groups.replace_groups(conn, [], [NewGroup(frozenset({a, b, c}), 0.0, True, "open", {a: "keep", b: "delete", c: "undecided"})])
    return {"a": a, "b": b, "c": c, "gid": gid, "arch": fa, "inbox": fi, "inbox_dir": inbox}


def test_list_filter_counts_and_detail(client, seeded):
    s = seeded
    listed = client.get("/api/p/a/groups").json()
    assert len(listed) == 1 and listed[0]["member_count"] == 3 and len(listed[0]["members"]) == 3
    member = next(m for m in listed[0]["members"] if m["file_id"] == id_to_str(s["b"]))
    assert member["decision"] == "delete" and member["role"] == "inbox" and member["relative_path"] == "b.jpg"
    assert client.get("/api/p/a/groups?status=resolved").json() == []
    assert client.get("/api/p/a/groups?status=bogus").status_code == 400
    assert len(client.get(f"/api/p/a/groups?folder_id={id_to_str(s['arch'])}&has_identical=true&min_size=3").json()) == 1
    assert client.get("/api/p/a/groups/counts").json() == {"open": 1, "auto_resolved": 0, "resolved": 0, "dismissed": 0, "dirty": 0}
    detail = client.get(f"/api/p/a/groups/{id_to_str(s['gid'])}").json()
    assert detail["group"]["id"] == id_to_str(s["gid"]) and len(detail["pairs"]) == 2
    assert client.get("/api/p/a/groups/00000000-0000-0000-0000-000000000000").status_code == 404


def test_resolve_requires_every_decision_then_trashes(client, seeded):
    s = seeded
    gid = id_to_str(s["gid"])
    partial_ = client.post(f"/api/p/a/groups/{gid}/resolve", json={"decisions": {id_to_str(s["a"]): "keep", id_to_str(s["b"]): "delete"}})
    assert partial_.status_code == 400 and id_to_str(s["c"]) in partial_.json()["detail"]
    full = client.post(f"/api/p/a/groups/{gid}/resolve", json={"decisions": {id_to_str(s["a"]): "keep", id_to_str(s["b"]): "delete", id_to_str(s["c"]): "keep"}})
    assert full.status_code == 200, full.text
    body = full.json()
    assert body["job"]["type"] == "apply_action" and body["job"]["params"]["trash"] == [id_to_str(s["b"])]
    finished = wait_for(client, "a", body["job"]["id"])
    assert finished["state"] == "done" and finished["progress"]["result"]["trashed"] == 1
    assert not (s["inbox_dir"] / "b.jpg").exists()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and client.get(f"/api/p/a/groups/{gid}").status_code == 200 \
            and client.get(f"/api/p/a/groups/{gid}").json()["group"]["status"] != "resolved":
        time.sleep(0.05)
    detail = client.get(f"/api/p/a/groups/{gid}")
    assert detail.status_code == 200 and detail.json()["group"]["status"] == "resolved"
    kept_pair = next(p for p in detail.json()["pairs"] if {p["file_a"], p["file_b"]} == {id_to_str(s["a"]), id_to_str(s["c"])})
    assert kept_pair["judgment"] == "related_keep" and kept_pair["judgment_origin"] == "user"


def test_dismiss_and_judgment(client, seeded):
    s = seeded
    gid = id_to_str(s["gid"])
    dismissed = client.post(f"/api/p/a/groups/{gid}/dismiss").json()
    assert dismissed["status"] == "dismissed"
    assert all(p["judgment"] == "related_keep" for p in client.get(f"/api/p/a/groups/{gid}").json()["pairs"])
    judged = client.put(f"/api/p/a/pairs/{id_to_str(s['a'])}/{id_to_str(s['c'])}/judgment", json={"judgment": "unrelated"})
    assert judged.status_code == 200 and judged.json()["judgment"] == "unrelated"
    assert client.put(f"/api/p/a/pairs/{id_to_str(s['b'])}/{id_to_str(s['c'])}/judgment", json={"judgment": "none"}).status_code == 404
    assert client.put(f"/api/p/a/pairs/{id_to_str(s['a'])}/{id_to_str(s['c'])}/judgment", json={"judgment": "maybe"}).status_code == 400
    queued = [j for j in client.get("/api/p/a/jobs").json() if j["type"] == "regroup"]
    assert queued and queued[0]["priority"] == 100


def test_file_detail_and_content(client, seeded):
    s = seeded
    fid = id_to_str(s["b"])
    detail = client.get(f"/api/p/a/files/{fid}").json()
    assert detail["relative_path"] == "b.jpg" and detail["group_id"] == id_to_str(s["gid"]) and len(detail["pairs"]) == 1
    content = client.get(f"/api/p/a/files/{fid}/content")
    assert content.status_code == 200 and content.content == b"same" and content.headers["content-type"].startswith("image/jpeg")
    (s["inbox_dir"] / "b.jpg").unlink()
    assert client.get(f"/api/p/a/files/{fid}/content").status_code == 404
    assert client.get("/api/p/a/files/00000000-0000-0000-0000-000000000000").status_code == 404
```

Run: `uv run pytest tests/integration/test_api_groups.py -q` — Expected: FAIL (routes missing).

- [ ] **Step 2: Implement**

`visualstore/api/groups.py`:

```python
"""Review queue, group detail, resolution and pair judgments (design §8.1 pages 3–4, §8.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import groups as groups_db
from visualstore.db import jobs as jobs_db
from visualstore.db import pairs as pairs_db
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}", tags=["groups"], dependencies=[Depends(require_auth)])


class Resolve(BaseModel):
    decisions: dict[str, str]


class Judgment(BaseModel):
    judgment: str


def _group_out(conn, row) -> dict:
    d = groups_db.group_to_dict(row)
    d["members"] = [groups_db.member_to_dict(m) for m in groups_db.members(conn, bytes(row["id"]))]
    return d


def _group_or_404(conn, group_id: bytes):
    row = groups_db.get_group(conn, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such group.")
    return row


def _member_ids(conn, group_id: bytes) -> set[bytes]:
    return {bytes(m["file_id"]) for m in groups_db.members(conn, group_id)}


def _inner_pairs(conn, member_ids: set[bytes]) -> list:
    return [
        p for p in pairs_db.edges_touching(conn, member_ids)
        if bytes(p["file_a"]) in member_ids and bytes(p["file_b"]) in member_ids
    ]


@router.get("/groups")
async def list_groups(
    request: Request, name: str = Depends(get_profile), status: str | None = None, folder_id: str | None = None,
    has_identical: bool | None = None, min_size: int | None = None, limit: int = 50, offset: int = 0,
) -> list[dict]:
    if status is not None and status not in groups_db.STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {groups_db.STATUSES}")
    conn = get_state(request).registry.conn(name)
    rows = groups_db.list_groups(
        conn, status=status, folder_id=parse_id(folder_id) if folder_id else None,
        has_identical=has_identical, min_size=min_size, limit=min(limit, 200), offset=offset,
    )
    return [_group_out(conn, r) for r in rows]


@router.get("/groups/counts")
async def group_counts(request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    return {**groups_db.count_by_status(conn), "dirty": groups_db.dirty_count(conn)}


@router.get("/groups/{group_id}")
async def get_group(group_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    row = _group_or_404(conn, gid)
    return {"group": _group_out(conn, row), "pairs": [pairs_db.pair_to_dict(p) for p in _inner_pairs(conn, _member_ids(conn, gid))]}


@router.post("/groups/{group_id}/resolve")
async def resolve_group(group_id: str, body: Resolve, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    _group_or_404(conn, gid)
    members = _member_ids(conn, gid)
    decisions = {parse_id(k): v for k, v in body.decisions.items()}
    if any(v not in ("keep", "delete") for v in decisions.values()):
        raise HTTPException(status_code=400, detail="Decisions must be 'keep' or 'delete'.")
    undecided = sorted(id_to_str(m) for m in members if m not in decisions)
    if undecided:
        raise HTTPException(status_code=400, detail=f"Every member needs a decision; missing: {', '.join(undecided)}")
    groups_db.set_decisions(conn, gid, {m: decisions[m] for m in members})
    kept = {m for m in members if decisions[m] == "keep"}
    for p in _inner_pairs(conn, kept):
        if p["judgment"] == "none":
            pairs_db.set_judgment(conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user")
    trash = [id_to_str(m) for m in members if decisions[m] == "delete"]
    job = None
    if trash:
        job_id = jobs_db.enqueue(conn, type="apply_action", kind="io", priority=PRIORITY_USER, params={"group_id": group_id, "trash": trash})
        job = jobs_db.to_dict(jobs_db.get(conn, job_id))
    else:
        groups_db.set_status(conn, gid, "resolved")
    return {"group": _group_out(conn, groups_db.get_group(conn, gid)), "job": job}


@router.post("/groups/{group_id}/dismiss")
async def dismiss_group(group_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    _group_or_404(conn, gid)
    for p in _inner_pairs(conn, _member_ids(conn, gid)):
        pairs_db.set_judgment(conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user")
    groups_db.set_decisions(conn, gid, {m: "keep" for m in _member_ids(conn, gid)})
    groups_db.set_status(conn, gid, "dismissed")
    return _group_out(conn, groups_db.get_group(conn, gid))


@router.put("/pairs/{file_a}/{file_b}/judgment")
async def judge_pair(file_a: str, file_b: str, body: Judgment, request: Request, name: str = Depends(get_profile)) -> dict:
    if body.judgment not in pairs_db.JUDGMENTS:
        raise HTTPException(status_code=400, detail=f"judgment must be one of {pairs_db.JUDGMENTS}")
    conn = get_state(request).registry.conn(name)
    a, b = parse_id(file_a), parse_id(file_b)
    if not pairs_db.set_judgment(conn, a, b, body.judgment, "user"):
        raise HTTPException(status_code=404, detail="No such pair.")
    groups_db.mark_dirty(conn, [a, b])
    if not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_USER)
    fa, fb = pairs_db.ordered(a, b)
    row = conn.execute("SELECT * FROM pair WHERE file_a = ? AND file_b = ?", (fa, fb)).fetchone()
    return pairs_db.pair_to_dict(row)
```

`visualstore/api/files.py`:

```python
"""File detail and original-content streaming (design §8.3 "open original")."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import files as files_db
from visualstore.db import folders as folders_db
from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db
from visualstore.ids import id_to_str

router = APIRouter(prefix="/api/p/{name}/files", tags=["files"], dependencies=[Depends(require_auth)])


def _file_and_folder(conn, file_id: bytes):
    row = files_db.get_file(conn, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such file.")
    return row, folders_db.get_folder(conn, bytes(row["folder_id"]))


@router.get("/{file_id}")
async def file_detail(file_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    d = files_db.file_to_dict(row, folder["path"])
    g = groups_db.group_of_file(conn, fid)
    d["group_id"] = id_to_str(bytes(g["id"])) if g else None
    d["pairs"] = [pairs_db.pair_to_dict(p) for p in pairs_db.pairs_of_file(conn, fid)]
    return d


@router.get("/{file_id}/content")
async def file_content(file_id: str, request: Request, name: str = Depends(get_profile)) -> FileResponse:
    conn = get_state(request).registry.conn(name)
    row, folder = _file_and_folder(conn, parse_id(file_id))
    path = Path(folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file is not on disk.")
    media_type = mimetypes.guess_type(row["name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=row["name"], content_disposition_type="inline")
```

`visualstore/app.py`: include `groups_api.router` and `files_api.router`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_api_groups.py tests/integration/test_api_async.py -q` — Expected: all passed.

```bash
git add visualstore/api/groups.py visualstore/api/files.py visualstore/app.py tests/integration/test_api_groups.py
git commit -m "feat: groups, pair judgment and file API

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: Trash, archive-move and v1-import API

**Files:**
- Create: `visualstore/api/trash.py`, `visualstore/api/operations.py`, `tests/integration/test_api_operations.py`
- Modify: `visualstore/app.py`

**Interfaces** (under `/api/p/{name}`, auth, `get_profile`, async):
- `GET /trash` → `[action_to_dict]` (restorable trash actions, newest first).
- `POST /trash/restore {action_ids: [...]}` → 201 `apply_action` job `{"restore": [...]}` at `PRIORITY_USER`.
- `POST /archive-move {source_folder_id, target_folder_id, directories?: []}` → 201 `archive_move` job at `PRIORITY_USER`; 404 for unknown Folders.
- `POST /import-v1 {db_path, folder_paths: {name: path}}` → 400 when the profile has Folders already; 201 `import_v1` job at `PRIORITY_USER`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_api_operations.py`:

```python
import hashlib

import pytest

from tests.fixtures.v1db import build
from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders
from visualstore.ids import id_to_str


@pytest.fixture
def seeded(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    conn = client.app.state.vs.registry.conn("a")
    inbox, arch = tmp_path / "inbox", tmp_path / "arch"
    (inbox / "set").mkdir(parents=True)
    arch.mkdir()
    fi = folders.create_folder(conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all")
    fa = folders.create_folder(conn, name="Arch", path=str(arch), role="archive", compare_scope="all")
    ext = folders.extension_map(conn)["jpg"]["id"]
    (inbox / "set" / "x.jpg").write_bytes(b"x")
    x = files.insert_file(conn, folder_id=fi, directory="set", name="x.jpg", extension_id=ext, size=1, mtime=1)
    files.set_fingerprint_result(conn, x, sha1=hashlib.sha1(b"x").digest(), state="fingerprinted")
    return {"conn": conn, "inbox": fi, "arch": fa, "inbox_dir": inbox, "arch_dir": arch, "x": x}


def test_trash_listing_and_restore(client, seeded):
    s = seeded
    job = client.post("/api/p/a/jobs", json={"type": "apply_action", "params": {"trash": [id_to_str(s["x"])]}}).json()
    assert wait_for(client, "a", job["id"])["state"] == "done"
    [entry] = client.get("/api/p/a/trash").json()
    assert entry["before"]["relative_path"] == "set/x.jpg" and entry["type"] == "trash"
    restore = client.post("/api/p/a/trash/restore", json={"action_ids": [entry["id"]]})
    assert restore.status_code == 201
    assert wait_for(client, "a", restore.json()["id"])["progress"]["result"]["restored"] == 1
    assert (s["inbox_dir"] / "set" / "x.jpg").read_bytes() == b"x"
    assert client.get("/api/p/a/trash").json() == []


def test_archive_move_endpoint(client, seeded):
    s = seeded
    resp = client.post("/api/p/a/archive-move", json={"source_folder_id": id_to_str(s["inbox"]), "target_folder_id": id_to_str(s["arch"]), "directories": ["set"]})
    assert resp.status_code == 201, resp.text
    job = wait_for(client, "a", resp.json()["id"])
    assert job["state"] == "done" and job["progress"]["result"]["moved"] == 1
    assert (s["arch_dir"] / "set" / "x.jpg").exists()
    assert client.post("/api/p/a/archive-move", json={"source_folder_id": "00000000-0000-0000-0000-000000000000", "target_folder_id": id_to_str(s["arch"])}).status_code == 404


def test_import_v1_endpoint(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "fresh"}).status_code == 201
    (tmp_path / "m" / "images").mkdir(parents=True)
    (tmp_path / "m" / "new").mkdir()
    build(tmp_path / "v1.db")
    body = {"db_path": str(tmp_path / "v1.db"), "folder_paths": {"Archive": str(tmp_path / "m" / "images"), "Inbox": str(tmp_path / "m" / "new")}}
    resp = client.post("/api/p/fresh/import-v1", json=body)
    assert resp.status_code == 201, resp.text
    job = wait_for(client, "fresh", resp.json()["id"])
    assert job["state"] == "done", job
    assert job["progress"]["result"]["files"] == 4
    assert len(client.get("/api/p/fresh/folders").json()) == 2
    assert client.post("/api/p/fresh/import-v1", json=body).status_code == 400
```

Run: `uv run pytest tests/integration/test_api_operations.py -q` — Expected: FAIL (routes missing).

- [ ] **Step 2: Implement**

`visualstore/api/trash.py`:

```python
"""Trash listing and restore (design §8.5)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import actions as actions_db
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}/trash", tags=["trash"], dependencies=[Depends(require_auth)])


class Restore(BaseModel):
    action_ids: list[str]


@router.get("")
async def list_trash(request: Request, name: str = Depends(get_profile)) -> list[dict]:
    conn = get_state(request).registry.conn(name)
    return [actions_db.action_to_dict(r) for r in actions_db.list_trash(conn)]


@router.post("/restore", status_code=201)
async def restore(body: Restore, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    for text in body.action_ids:
        parse_id(text)
    job_id = jobs_db.enqueue(conn, type="apply_action", kind="io", priority=PRIORITY_USER, params={"restore": body.action_ids})
    return jobs_db.to_dict(jobs_db.get(conn, job_id))
```

`visualstore/api/operations.py`:

```python
"""Long-running operations started from the UI: archive move and v1 import (design §8.5, §10)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(prefix="/api/p/{name}", tags=["operations"], dependencies=[Depends(require_auth)])


class ArchiveMove(BaseModel):
    source_folder_id: str
    target_folder_id: str
    directories: list[str] = []


class ImportV1(BaseModel):
    db_path: str
    folder_paths: dict[str, str]


@router.post("/archive-move", status_code=201)
async def archive_move(body: ArchiveMove, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    for text in (body.source_folder_id, body.target_folder_id):
        if folders_db.get_folder(conn, parse_id(text)) is None:
            raise HTTPException(status_code=404, detail="No such Folder.")
    job_id = jobs_db.enqueue(conn, type="archive_move", kind="io", priority=PRIORITY_USER, params=body.model_dump())
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/import-v1", status_code=201)
async def import_v1(body: ImportV1, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    if folders_db.list_folders(conn):
        raise HTTPException(status_code=400, detail="Import needs an empty profile (no Folders yet).")
    job_id = jobs_db.enqueue(conn, type="import_v1", kind="io", priority=PRIORITY_USER, params=body.model_dump())
    return jobs_db.to_dict(jobs_db.get(conn, job_id))
```

`visualstore/app.py`: include both routers.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration/test_api_operations.py -q` — Expected: 3 passed.

```bash
git add visualstore/api/trash.py visualstore/api/operations.py visualstore/app.py tests/integration/test_api_operations.py
git commit -m "feat: trash, archive-move and v1 import API

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 18: Overview additions

**Files:**
- Modify: `visualstore/api/overview.py`, `tests/integration/test_api_profile_scoped.py`

**Interfaces:**
- `GET /overview` gains `"folders": [{id, name, role, enabled, counts: {state: n}}]`, `"groups": count_by_status`, `"dirty": n`. Existing keys unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_api_profile_scoped.py`:

```python
def test_overview_lists_folders_and_group_counts(client, tmp_path):
    setup_profile(client)
    (tmp_path / "lib").mkdir()
    folder = client.post("/api/p/a/folders", json={"name": "Lib", "path": str(tmp_path / "lib"), "role": "inbox", "compare_scope": "all"}).json()
    overview = client.get("/api/p/a/overview").json()
    assert overview["folders"] == [{"id": folder["id"], "name": "Lib", "role": "inbox", "enabled": True, "counts": {}}]
    assert overview["groups"] == {"open": 0, "auto_resolved": 0, "resolved": 0, "dismissed": 0}
    assert overview["dirty"] == 0
```

Run: `uv run pytest tests/integration/test_api_profile_scoped.py -q -k overview_lists` — Expected: FAIL (`KeyError: 'folders'`).

- [ ] **Step 2: Implement**

In `visualstore/api/overview.py` import `files as files_db`, `folders as folders_db`, `groups as groups_db` and add to the returned dict:

```python
        "folders": [
            {
                "id": id_to_str(bytes(f["id"])), "name": f["name"], "role": f["role"], "enabled": bool(f["enabled"]),
                "counts": counts.get(id_to_str(bytes(f["id"])), {}),
            }
            for f in folders_db.list_folders(conn)
        ],
        "groups": groups_db.count_by_status(conn),
        "dirty": groups_db.dirty_count(conn),
```

with `counts = files_db.count_by_folder_and_state(conn)` computed first and `from visualstore.ids import id_to_str`.

- [ ] **Step 3: Run the tests, then commit**

Run: `uv run pytest tests/integration -q` — Expected: all passed.

```bash
git add visualstore/api/overview.py tests/integration/test_api_profile_scoped.py
git commit -m "feat: overview shows per-folder file states and group counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 19: Frontend types, navigation, and the Folders page

**Files:**
- Modify: `web/src/types.ts`, `web/src/components/Layout.tsx`, `web/src/App.tsx`
- Create: `web/src/pages/Folders.tsx`, `web/src/format.ts`, `web/src/format.test.ts`

**Interfaces:**
- `types.ts` adds: `Folder {id, name, path, role: "archive"|"inbox", compare_scope, enabled, priority, created_at, ignored: IgnoredDirectory[]}`, `IgnoredDirectory {id, directory, recursive}`, `Extension {id, extension, media_type, enabled}`, `GroupMember {file_id, decision: "keep"|"delete"|"undecided", directory, name, size, sha1, state, folder_id, folder_name, folder_path, role, folder_priority, relative_path, absolute_path}`, `Group {id, member_count, min_distance, has_identical, status, updated_at, members: GroupMember[]}`, `Pair {file_a, file_b, distance, source, judgment, judged_at, judgment_origin, model_id}`, `GroupDetail {group: Group, pairs: Pair[]}`, `TrashEntry {id, type, file_id, before: Record<string, unknown>, after: Record<string, unknown>, at}`, `Overview` gains `folders: {id, name, role, enabled, counts: Record<string, number>}[]`, `groups: Record<string, number>`, `dirty: number`.
- `format.ts`: `formatBytes(n)` (moved out of Profiles.tsx), `formatWhen(iso | null)`.
- Routes: `/p/:name/folders`, `/p/:name/groups`, `/p/:name/groups/:groupId`, `/p/:name/jobs`. Layout shows profile nav links (Overview · Folders · Groups · Jobs) when a profile is in the URL.

- [ ] **Step 1: Write the failing unit test**

`web/src/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatBytes, formatWhen } from "./format";

describe("format", () => {
  it("formats byte counts", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
  it("formats missing times as never", () => {
    expect(formatWhen(null)).toBe("never");
    expect(formatWhen("2026-11-06T10:00:00+00:00")).not.toBe("never");
  });
});
```

Run: `cd web && npm test` — Expected: FAIL (module `./format` missing).

- [ ] **Step 2: Implement shared pieces**

`web/src/format.ts`:

```ts
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(1)} ${units[i]}`;
}

export function formatWhen(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never";
}
```

Remove the local `formatBytes` from `Profiles.tsx` and import it from `../format`; replace `when()` in `Overview.tsx` with `formatWhen`.

Append to `web/src/types.ts`:

```ts
export interface IgnoredDirectory {
  id: string;
  directory: string;
  recursive: boolean;
}

export interface Folder {
  id: string;
  name: string;
  path: string;
  role: "archive" | "inbox";
  compare_scope: "all" | "other_directories" | "other_folders";
  enabled: boolean;
  priority: number;
  created_at: string;
  ignored: IgnoredDirectory[];
}

export interface Extension {
  id: string;
  extension: string;
  media_type: "image" | "animated" | "video" | "other";
  enabled: boolean;
}

export type Decision = "keep" | "delete" | "undecided";

export interface GroupMember {
  file_id: string;
  decision: Decision;
  directory: string;
  name: string;
  size: number;
  sha1: string | null;
  state: string;
  folder_id: string;
  folder_name: string;
  folder_path: string;
  role: "archive" | "inbox";
  folder_priority: number;
  relative_path: string;
  absolute_path: string;
}

export interface Group {
  id: string;
  member_count: number;
  min_distance: number | null;
  has_identical: boolean;
  status: "open" | "auto_resolved" | "resolved" | "dismissed";
  updated_at: string;
  members: GroupMember[];
}

export interface Pair {
  file_a: string;
  file_b: string;
  distance: number | null;
  source: "sha1" | "vector" | "import";
  judgment: "none" | "related_keep" | "unrelated";
  judged_at: string | null;
  judgment_origin: "user" | "import_v1" | "rule" | null;
  model_id: string | null;
}

export interface GroupDetail {
  group: Group;
  pairs: Pair[];
}

export interface TrashEntry {
  id: string;
  type: string;
  file_id: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  at: string;
}
```

and extend `Overview` with `folders: { id: string; name: string; role: "archive" | "inbox"; enabled: boolean; counts: Record<string, number> }[]; groups: Record<string, number>; dirty: number;`.

`web/src/App.tsx`: add routes `<Route path="/p/:name/folders" element={<Folders />} />`, `<Route path="/p/:name/groups" element={<Groups />} />`, `<Route path="/p/:name/groups/:groupId" element={<GroupDetail />} />`, `<Route path="/p/:name/jobs" element={<Jobs />} />` (Groups/GroupDetail/Jobs are Tasks 20–22; create them as placeholders returning `null` in this task so the app compiles, and replace them in their tasks — the e2e in Task 23 is what verifies the real pages).

`web/src/components/Layout.tsx`: inside `<nav>` before the Profiles link, when `name` is set:

```tsx
        {name && (
          <>
            <NavLink to={`/p/${name}/overview`}>Overview</NavLink>
            <NavLink to={`/p/${name}/folders`}>Folders</NavLink>
            <NavLink to={`/p/${name}/groups`}>Groups</NavLink>
            <NavLink to={`/p/${name}/jobs`}>Jobs</NavLink>
          </>
        )}
```

(import `NavLink` from react-router-dom; add `.topbar nav a.active { font-weight: 600; }` to `styles.css`).

- [ ] **Step 3: Implement the Folders page**

`web/src/pages/Folders.tsx`:

```tsx
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, del, get, patch, post, put } from "../api";
import type { Extension, Folder } from "../types";

const SCOPES: Folder["compare_scope"][] = ["all", "other_directories", "other_folders"];

export default function Folders() {
  const { name = "" } = useParams();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", path: "", role: "inbox" as Folder["role"], compare_scope: "all" as Folder["compare_scope"], priority: 0 });
  const [ignoredInput, setIgnoredInput] = useState<Record<string, string>>({});
  const [extForm, setExtForm] = useState({ extension: "", media_type: "image" as Extension["media_type"] });

  const reload = useCallback(async () => {
    try {
      setFolders(await get<Folder[]>(`/api/p/${name}/folders`));
      setExtensions(await get<Extension[]>(`/api/p/${name}/extensions`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load Folders.");
    }
  }, [name]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      await reload();
      if (done) setNotice(done);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      await post(`/api/p/${name}/folders`, form);
      setForm({ name: "", path: "", role: "inbox", compare_scope: "all", priority: 0 });
    }, "Folder created.");
  }

  return (
    <section>
      <h1>Folders</h1>
      {folders.length === 0 && <p className="muted">No Folders yet. A Folder is one library root inside the container, for example /media/images.</p>}
      {folders.map((f) => (
        <div className="card" key={f.id}>
          <h2>
            {f.name} <code>{f.path}</code>
          </h2>
          <p className="muted">
            {f.role} · compare with {f.compare_scope.replace(/_/g, " ")} · priority {f.priority} · {f.enabled ? "enabled" : "disabled"}
          </p>
          <div className="actions-row">
            <button type="button" onClick={() => void run(() => post(`/api/p/${name}/folders/${f.id}/scan`), "Scan queued.")} disabled={!f.enabled}>
              Scan now
            </button>
            <select aria-label={`Role of ${f.name}`} value={f.role} onChange={(e) => void run(() => patch(`/api/p/${name}/folders/${f.id}`, { role: e.target.value }))}>
              <option value="inbox">inbox</option>
              <option value="archive">archive</option>
            </select>
            <select aria-label={`Compare scope of ${f.name}`} value={f.compare_scope} onChange={(e) => void run(() => patch(`/api/p/${name}/folders/${f.id}`, { compare_scope: e.target.value }))}>
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <label className="inline-label">
              Priority
              <input type="number" value={f.priority} style={{ width: "4rem" }} onChange={(e) => void run(() => patch(`/api/p/${name}/folders/${f.id}`, { priority: Number(e.target.value) }))} />
            </label>
            <label className="inline-label">
              <input type="checkbox" checked={f.enabled} onChange={(e) => void run(() => patch(`/api/p/${name}/folders/${f.id}`, { enabled: e.target.checked }))} /> Enabled
            </label>
            <button type="button" className="danger" onClick={() => { if (window.confirm(`Remove Folder ${f.name} from this profile? Files on disk are not touched.`)) void run(() => del(`/api/p/${name}/folders/${f.id}`), "Folder removed."); }}>
              Remove
            </button>
          </div>
          <h3>Ignored directories</h3>
          <ul>
            {f.ignored.map((i) => (
              <li key={i.id}>
                <code>{i.directory}</code> {i.recursive ? "(and subdirectories)" : "(only this directory)"}{" "}
                <button type="button" className="link" onClick={() => void run(() => del(`/api/p/${name}/folders/${f.id}/ignored/${i.id}`))}>
                  remove
                </button>
              </li>
            ))}
          </ul>
          <form className="inline" onSubmit={(e) => { e.preventDefault(); void run(async () => { await post(`/api/p/${name}/folders/${f.id}/ignored`, { directory: ignoredInput[f.id] ?? "", recursive: true }); setIgnoredInput((prev) => ({ ...prev, [f.id]: "" })); }); }}>
            <label>
              Ignore directory (relative)
              <input value={ignoredInput[f.id] ?? ""} onChange={(e) => setIgnoredInput((prev) => ({ ...prev, [f.id]: e.target.value }))} placeholder="Thumbs" required />
            </label>
            <button type="submit">Add</button>
          </form>
        </div>
      ))}

      <h2>New Folder</h2>
      <form className="inline" onSubmit={create}>
        <label>
          Name
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </label>
        <label>
          Container path
          <input value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} placeholder="/media/images" required />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Folder["role"] })}>
            <option value="inbox">inbox (new files)</option>
            <option value="archive">archive (storage)</option>
          </select>
        </label>
        <label>
          Compare with
          <select value={form.compare_scope} onChange={(e) => setForm({ ...form, compare_scope: e.target.value as Folder["compare_scope"] })}>
            {SCOPES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Create Folder</button>
      </form>

      <h2>Extensions</h2>
      <table>
        <thead>
          <tr>
            <th>Extension</th>
            <th>Media type</th>
            <th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {extensions.map((x) => (
            <tr key={x.id}>
              <td>
                <code>{x.extension}</code>
              </td>
              <td>{x.media_type}</td>
              <td>
                <input type="checkbox" aria-label={`Enable ${x.extension}`} checked={x.enabled} onChange={(e) => void run(() => patch(`/api/p/${name}/extensions/${x.id}`, { enabled: e.target.checked }))} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); void run(async () => { await put(`/api/p/${name}/extensions`, extForm); setExtForm({ extension: "", media_type: "image" }); }); }}>
        <label>
          Extension
          <input value={extForm.extension} onChange={(e) => setExtForm({ ...extForm, extension: e.target.value })} placeholder="jxl" required />
        </label>
        <label>
          Media type
          <select value={extForm.media_type} onChange={(e) => setExtForm({ ...extForm, media_type: e.target.value as Extension["media_type"] })}>
            {["image", "animated", "video", "other"].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Add extension</button>
      </form>

      {notice && <p className="muted" role="status">{notice}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
```

Add to `styles.css`: `.actions-row { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; margin: 0.5rem 0; } .inline-label { display: inline-flex; gap: 0.3rem; align-items: center; } .card + .card { margin-top: 1rem; } h3 { font-size: 0.95rem; margin: 0.75rem 0 0.25rem; }`.

- [ ] **Step 4: Verify and commit**

Run: `cd web && npm test && npm run lint && npm run build` — Expected: tests pass (format tests added), no type errors, build succeeds.

```bash
git add web/src
git commit -m "feat(web): folder management page, shared formatting, profile navigation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 20: Review queue page

**Files:**
- Create: `web/src/pages/Groups.tsx` (replaces the placeholder)

**Interfaces:**
- Shows `GET /groups/counts` as a header line and the filtered `GET /groups` list; filters: status (default `open`), Folder (from `GET /folders`), "contains byte-identical", minimum size. Each row: member count, Folders involved, first member's relative path, status, updated time, link to detail. "Load more" pagination with `offset`. Refreshes every 5 s while the tab is visible.

- [ ] **Step 1: Implement**

`web/src/pages/Groups.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get } from "../api";
import { formatWhen } from "../format";
import type { Folder, Group } from "../types";

const PAGE = 50;

export default function Groups() {
  const { name = "" } = useParams();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState<string>("open");
  const [folderId, setFolderId] = useState<string>("");
  const [identical, setIdentical] = useState(false);
  const [minSize, setMinSize] = useState(2);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const query = useCallback(
    (off: number) => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(off), min_size: String(minSize) });
      if (status) q.set("status", status);
      if (folderId) q.set("folder_id", folderId);
      if (identical) q.set("has_identical", "true");
      return `/api/p/${name}/groups?${q.toString()}`;
    },
    [name, status, folderId, identical, minSize],
  );

  const load = useCallback(async () => {
    try {
      const [first, c, f] = await Promise.all([
        get<Group[]>(query(0)),
        get<Record<string, number>>(`/api/p/${name}/groups/counts`),
        get<Folder[]>(`/api/p/${name}/folders`),
      ]);
      setGroups(first);
      setOffset(first.length);
      setCounts(c);
      setFolders(f);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load groups.");
    }
  }, [name, query]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function more() {
    const next = await get<Group[]>(query(offset));
    setGroups((prev) => [...prev, ...next]);
    setOffset(offset + next.length);
  }

  return (
    <section>
      <h1>Groups</h1>
      <p className="muted">
        {counts.open ?? 0} open · {counts.auto_resolved ?? 0} automatic · {counts.resolved ?? 0} resolved · {counts.dismissed ?? 0} dismissed
        {counts.dirty ? ` · ${counts.dirty} files waiting for regrouping` : ""}
      </p>
      <form className="inline" onSubmit={(e) => e.preventDefault()}>
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">any</option>
            {["open", "auto_resolved", "resolved", "dismissed"].map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Folder
          <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
            <option value="">any</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-label">
          <input type="checkbox" checked={identical} onChange={(e) => setIdentical(e.target.checked)} /> byte-identical only
        </label>
        <label>
          Min size
          <input type="number" min={2} value={minSize} style={{ width: "4rem" }} onChange={(e) => setMinSize(Number(e.target.value) || 2)} />
        </label>
      </form>
      {groups.length === 0 ? (
        <p className="muted">No groups match.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Files</th>
              <th>Folders</th>
              <th>First member</th>
              <th>Status</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id} className={`group-${g.status}`}>
                <td>
                  <Link to={`/p/${name}/groups/${g.id}`}>{g.member_count} files</Link>
                  {g.has_identical && <span className="tag">identical</span>}
                </td>
                <td>{[...new Set(g.members.map((m) => m.folder_name))].join(", ")}</td>
                <td className="muted">{g.members[0]?.relative_path}</td>
                <td>{g.status.replace("_", " ")}</td>
                <td className="muted">{formatWhen(g.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {groups.length >= PAGE && groups.length === offset && (
        <button type="button" onClick={() => void more()}>
          Load more
        </button>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
```

Add `.tag { margin-left: 0.5rem; font-size: 0.75rem; padding: 0 0.4rem; border: 1px solid currentColor; border-radius: 0.5rem; opacity: 0.8; }` to `styles.css`.

- [ ] **Step 2: Verify and commit**

Run: `cd web && npm run lint && npm run build` — Expected: clean.

```bash
git add web/src
git commit -m "feat(web): review queue with filters and counts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 21: Group detail page with keyboard resolution

**Files:**
- Create: `web/src/pages/GroupDetail.tsx` (replaces the placeholder), `web/src/pages/groupKeys.ts`, `web/src/pages/groupKeys.test.ts`

**Interfaces:**
- `groupKeys.ts` (pure, unit-tested): `applyKey(state: KeyState, key: string) -> KeyState | {action: "confirm" | "skip" | "unrelated"}` where `KeyState = {members: {file_id, decision}[], selected: number, anchor: number | null}`; digits `1`–`9` select a member; `k` / `d` set the selected member's decision; `a` sets the anchor to the selected member (for pair judgments); `u` → `{action: "unrelated"}` when an anchor differs from the selected member; `Enter` → `{action: "confirm"}` only when every member is decided; `Escape` → `{action: "skip"}`; `ArrowDown`/`ArrowUp` move the selection. Everything else returns the state unchanged.
- Page: loads `GET /groups/{id}`; lists members (role tag, Folder, relative path, size, byte-identical mark, "open original" link to `/api/p/{name}/files/{id}/content` in a new tab; the image comparison pane arrives in milestone 4); decision buttons per member; keyboard per `groupKeys`; **Confirm** → `POST /groups/{id}/resolve`, then navigates to the next open group (`GET /groups?status=open&limit=2`, skipping the current id) or back to the queue; **Skip** → next open group; **Dismiss** → `POST /groups/{id}/dismiss`; **Mark unrelated** (anchor + selected) → `PUT /pairs/{a}/{b}/judgment {unrelated}` then reloads (the group may have split; if the current group disappears, go to the queue). A help line lists the keys.

- [ ] **Step 1: Write the failing unit tests**

`web/src/pages/groupKeys.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyKey, type KeyState } from "./groupKeys";

const base: KeyState = {
  members: [
    { file_id: "a", decision: "keep" },
    { file_id: "b", decision: "undecided" },
    { file_id: "c", decision: "delete" },
  ],
  selected: 0,
  anchor: null,
};

describe("applyKey", () => {
  it("selects members with digits and arrows", () => {
    expect((applyKey(base, "2") as KeyState).selected).toBe(1);
    expect((applyKey(base, "9") as KeyState).selected).toBe(0); // out of range: unchanged
    expect((applyKey(base, "ArrowDown") as KeyState).selected).toBe(1);
    expect((applyKey({ ...base, selected: 2 }, "ArrowDown") as KeyState).selected).toBe(2);
    expect((applyKey(base, "ArrowUp") as KeyState).selected).toBe(0);
  });

  it("k and d set the selected decision", () => {
    const s = applyKey({ ...base, selected: 1 }, "k") as KeyState;
    expect(s.members[1].decision).toBe("keep");
    expect((applyKey(s, "d") as KeyState).members[1].decision).toBe("delete");
  });

  it("Enter confirms only when every member is decided", () => {
    expect(applyKey(base, "Enter")).toEqual(base);
    const decided = applyKey({ ...base, selected: 1 }, "d") as KeyState;
    expect(applyKey(decided, "Enter")).toEqual({ action: "confirm" });
  });

  it("Escape skips; a sets the anchor; u asks for unrelated only across two members", () => {
    expect(applyKey(base, "Escape")).toEqual({ action: "skip" });
    expect(applyKey(base, "u")).toEqual(base);
    const anchored = applyKey(base, "a") as KeyState;
    expect(anchored.anchor).toBe(0);
    expect(applyKey(anchored, "u")).toEqual(anchored);
    expect(applyKey({ ...anchored, selected: 2 }, "u")).toEqual({ action: "unrelated" });
  });

  it("ignores other keys", () => {
    expect(applyKey(base, "x")).toEqual(base);
  });
});
```

Run: `cd web && npm test` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement the key model**

`web/src/pages/groupKeys.ts`:

```ts
import type { Decision } from "../types";

export interface KeyMember {
  file_id: string;
  decision: Decision;
}

export interface KeyState {
  members: KeyMember[];
  selected: number;
  anchor: number | null;
}

export type KeyAction = { action: "confirm" | "skip" | "unrelated" };

function withDecision(state: KeyState, decision: Decision): KeyState {
  return {
    ...state,
    members: state.members.map((m, i) => (i === state.selected ? { ...m, decision } : m)),
  };
}

export function applyKey(state: KeyState, key: string): KeyState | KeyAction {
  if (/^[1-9]$/.test(key)) {
    const index = Number(key) - 1;
    return index < state.members.length ? { ...state, selected: index } : state;
  }
  switch (key) {
    case "ArrowDown":
      return { ...state, selected: Math.min(state.selected + 1, state.members.length - 1) };
    case "ArrowUp":
      return { ...state, selected: Math.max(state.selected - 1, 0) };
    case "k":
      return withDecision(state, "keep");
    case "d":
      return withDecision(state, "delete");
    case "a":
      return { ...state, anchor: state.selected };
    case "u":
      return state.anchor !== null && state.anchor !== state.selected ? { action: "unrelated" } : state;
    case "Enter":
      return state.members.every((m) => m.decision !== "undecided") ? { action: "confirm" } : state;
    case "Escape":
      return { action: "skip" };
    default:
      return state;
  }
}
```

Run: `cd web && npm test` — Expected: pass.

- [ ] **Step 3: Implement the page**

`web/src/pages/GroupDetail.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import { formatBytes } from "../format";
import type { Decision, Group, GroupDetail as Detail } from "../types";
import { applyKey, type KeyState } from "./groupKeys";

export default function GroupDetail() {
  const { name = "", groupId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [keys, setKeys] = useState<KeyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await get<Detail>(`/api/p/${name}/groups/${groupId}`);
      setDetail(d);
      setKeys({ members: d.group.members.map((m) => ({ file_id: m.file_id, decision: m.decision })), selected: 0, anchor: null });
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) navigate(`/p/${name}/groups`, { replace: true });
      else setError(err instanceof ApiError ? err.message : "Could not load the group.");
    }
  }, [name, groupId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  const goNext = useCallback(async () => {
    const next = await get<Group[]>(`/api/p/${name}/groups?status=open&limit=2`);
    const target = next.find((g) => g.id !== groupId);
    navigate(target ? `/p/${name}/groups/${target.id}` : `/p/${name}/groups`);
  }, [name, groupId, navigate]);

  const confirm = useCallback(async () => {
    if (!keys || busy) return;
    setBusy(true);
    try {
      const decisions = Object.fromEntries(keys.members.map((m) => [m.file_id, m.decision]));
      await post(`/api/p/${name}/groups/${groupId}/resolve`, { decisions });
      await goNext();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve the group.");
    } finally {
      setBusy(false);
    }
  }, [keys, busy, name, groupId, goNext]);

  const markUnrelated = useCallback(async () => {
    if (!keys || keys.anchor === null) return;
    const a = keys.members[keys.anchor].file_id;
    const b = keys.members[keys.selected].file_id;
    try {
      await put(`/api/p/${name}/pairs/${a}/${b}/judgment`, { judgment: "unrelated" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not mark the pair.");
    }
  }, [keys, name, load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!keys || (e.target as HTMLElement)?.tagName === "INPUT") return;
      const result = applyKey(keys, e.key);
      if ("action" in result) {
        e.preventDefault();
        if (result.action === "confirm") void confirm();
        else if (result.action === "skip") void goNext();
        else void markUnrelated();
      } else if (result !== keys) {
        e.preventDefault();
        setKeys(result);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keys, confirm, goNext, markUnrelated]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!detail || !keys) return <p className="muted">Loading…</p>;

  const { group } = detail;
  const decided = keys.members.every((m) => m.decision !== "undecided");
  const setDecision = (index: number, decision: Decision) =>
    setKeys({ ...keys, selected: index, members: keys.members.map((m, i) => (i === index ? { ...m, decision } : m)) });

  return (
    <section>
      <h1>
        Group of {group.member_count} files {group.has_identical && <span className="tag">byte-identical</span>}{" "}
        <span className="tag">{group.status.replace("_", " ")}</span>
      </h1>
      <p className="muted">
        Keys: 1–9 select · ↑/↓ move · k keep · d delete · a anchor · u mark anchor+selected unrelated · Enter confirm · Esc skip
      </p>
      <ol className="members">
        {group.members.map((m, i) => {
          const decision = keys.members[i].decision;
          return (
            <li key={m.file_id} className={`member member-${decision} ${i === keys.selected ? "selected" : ""} ${i === keys.anchor ? "anchor" : ""}`} onClick={() => setKeys({ ...keys, selected: i })}>
              <div>
                <span className="tag">{m.role}</span> <strong>{m.folder_name}</strong> <code>{m.relative_path}</code>
                <span className="muted"> · {formatBytes(m.size)}</span>{" "}
                <a href={`/api/p/${name}/files/${m.file_id}/content`} target="_blank" rel="noreferrer">
                  open original
                </a>
              </div>
              <div className="actions-row">
                <button type="button" className={decision === "keep" ? "active" : ""} onClick={() => setDecision(i, "keep")}>
                  Keep
                </button>
                <button type="button" className={decision === "delete" ? "active danger" : "danger"} onClick={() => setDecision(i, "delete")}>
                  Delete
                </button>
                <button type="button" onClick={() => setKeys({ ...keys, anchor: i })}>
                  Anchor
                </button>
              </div>
            </li>
          );
        })}
      </ol>
      <div className="actions-row">
        <button type="button" onClick={() => void confirm()} disabled={!decided || busy || group.status !== "open"}>
          Confirm (Enter)
        </button>
        <button type="button" onClick={() => void goNext()}>
          Skip (Esc)
        </button>
        <button type="button" onClick={() => void markUnrelated()} disabled={keys.anchor === null || keys.anchor === keys.selected}>
          Mark unrelated (u)
        </button>
        <button type="button" onClick={() => void (async () => { await post(`/api/p/${name}/groups/${groupId}/dismiss`); await goNext(); })()} disabled={group.status !== "open"}>
          Dismiss group
        </button>
      </div>
      <p className="muted">{detail.pairs.length} pairs in this group. Image comparison arrives in milestone 4.</p>
    </section>
  );
}
```

Add to `styles.css`: `.members { list-style: decimal; padding-left: 1.5rem; } .member { padding: 0.5rem; border: 1px solid transparent; border-radius: 0.4rem; } .member.selected { border-color: color-mix(in srgb, currentColor 50%, transparent); } .member.anchor { background: color-mix(in srgb, currentColor 8%, transparent); } .member-delete > div:first-child { opacity: 0.6; text-decoration: line-through; } button.active { outline: 2px solid currentColor; }`.

- [ ] **Step 4: Verify and commit**

Run: `cd web && npm test && npm run lint && npm run build` — Expected: clean.

```bash
git add web/src
git commit -m "feat(web): group detail with keyboard resolution

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 22: Jobs, settings, trash, archive move and import page

**Files:**
- Create: `web/src/pages/Jobs.tsx` (replaces the placeholder)

**Interfaces:**
- Four sections on one page (design §8.1 page 5), each self-contained and refreshing every 3 s while visible:
  - **Jobs**: table of `GET /jobs?limit=100` with state filter, progress, error, attempts; Retry (`POST /jobs/{id}/retry`) on failed, Cancel on queued.
  - **Settings**: every key of `GET /settings` rendered by value type (checkbox for booleans, number input for numbers) with `PUT /settings/{key}` on change; server errors shown inline.
  - **Trash**: `GET /trash` rows (relative path, Folder, when) with per-row and select-all Restore (`POST /trash/restore`).
  - **Operations**: Archive move form (source inbox Folder select, target archive Folder select, optional directories textarea one per line) → `POST /archive-move`; Import v1 form (db path, one row per v1 Folder name → container path, "Add row") → `POST /import-v1`; both show the created job id and a link to the job list.

- [ ] **Step 1: Implement**

`web/src/pages/Jobs.tsx`:

```tsx
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import { formatWhen } from "../format";
import type { Folder, Job, TrashEntry } from "../types";

type Settings = Record<string, boolean | number>;

export default function Jobs() {
  const { name = "" } = useParams();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stateFilter, setStateFilter] = useState("");
  const [settings, setSettings] = useState<Settings>({});
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [selectedTrash, setSelectedTrash] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [move, setMove] = useState({ source_folder_id: "", target_folder_id: "", directories: "" });
  const [imp, setImp] = useState({ db_path: "", rows: [{ name: "", path: "" }] });

  const load = useCallback(async () => {
    try {
      const q = stateFilter ? `?state=${stateFilter}&limit=100` : "?limit=100";
      const [j, s, t, f] = await Promise.all([
        get<Job[]>(`/api/p/${name}/jobs${q}`),
        get<Settings>(`/api/p/${name}/settings`),
        get<TrashEntry[]>(`/api/p/${name}/trash`),
        get<Folder[]>(`/api/p/${name}/folders`),
      ]);
      setJobs(j);
      setSettings(s);
      setTrash(t);
      setFolders(f);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load.");
    }
  }, [name, stateFilter]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
      if (done) setNotice(done);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    }
  }

  function submitMove(e: FormEvent) {
    e.preventDefault();
    const directories = move.directories.split("\n").map((d) => d.trim()).filter(Boolean);
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/archive-move`, { ...move, directories });
      setNotice(`Archive move queued as job ${job.id}.`);
    });
  }

  function submitImport(e: FormEvent) {
    e.preventDefault();
    const folder_paths = Object.fromEntries(imp.rows.filter((r) => r.name && r.path).map((r) => [r.name, r.path]));
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/import-v1`, { db_path: imp.db_path, folder_paths });
      setNotice(`Import queued as job ${job.id}. Watch the job list for progress.`);
    });
  }

  const inboxes = folders.filter((f) => f.role === "inbox");
  const archives = folders.filter((f) => f.role === "archive");

  return (
    <section>
      <h1>Jobs</h1>
      <label>
        State
        <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
          <option value="">any</option>
          {["queued", "running", "done", "failed", "cancelled"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </label>
      <table>
        <thead>
          <tr><th>Type</th><th>State</th><th>Progress</th><th>Error</th><th>Created</th><th></th></tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className={`job-${j.state}`}>
              <td>{j.type}</td>
              <td>{j.state}{j.attempts > 0 && ` (attempt ${j.attempts})`}</td>
              <td className="muted">{j.progress.total ? `${j.progress.done ?? 0}/${j.progress.total} ` : ""}{j.progress.message ?? ""}</td>
              <td className="muted">{j.error ?? ""}</td>
              <td className="muted">{formatWhen(j.created_at)}</td>
              <td className="actions">
                {j.state === "failed" && <button type="button" onClick={() => void run(() => post(`/api/p/${name}/jobs/${j.id}/retry`))}>Retry</button>}
                {j.state === "queued" && <button type="button" onClick={() => void run(() => post(`/api/p/${name}/jobs/${j.id}/cancel`))}>Cancel</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Settings</h2>
      <table>
        <tbody>
          {Object.entries(settings).map(([key, value]) => (
            <tr key={key}>
              <td><code>{key}</code></td>
              <td>
                {typeof value === "boolean" ? (
                  <input type="checkbox" aria-label={key} checked={value} onChange={(e) => void run(() => put(`/api/p/${name}/settings/${key}`, { value: e.target.checked }))} />
                ) : (
                  <input type="number" aria-label={key} defaultValue={value} step={Number.isInteger(value) ? 1 : 0.01} onBlur={(e) => { const n = Number(e.target.value); if (n !== value) void run(() => put(`/api/p/${name}/settings/${key}`, { value: Number.isInteger(value) ? Math.round(n) : n })); }} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Trash</h2>
      {trash.length === 0 ? (
        <p className="muted">Nothing in the trash.</p>
      ) : (
        <>
          <div className="actions-row">
            <button type="button" onClick={() => setSelectedTrash(new Set(trash.map((t) => t.id)))}>Select all</button>
            <button type="button" disabled={selectedTrash.size === 0} onClick={() => void run(async () => { await post(`/api/p/${name}/trash/restore`, { action_ids: [...selectedTrash] }); setSelectedTrash(new Set()); }, "Restore queued.")}>
              Restore selected ({selectedTrash.size})
            </button>
          </div>
          <table>
            <tbody>
              {trash.map((t) => (
                <tr key={t.id}>
                  <td><input type="checkbox" aria-label={`select ${String(t.before.relative_path)}`} checked={selectedTrash.has(t.id)} onChange={(e) => { const next = new Set(selectedTrash); if (e.target.checked) next.add(t.id); else next.delete(t.id); setSelectedTrash(next); }} /></td>
                  <td><code>{String(t.before.relative_path)}</code></td>
                  <td className="muted">{formatWhen(t.at)}</td>
                  <td className="actions"><button type="button" onClick={() => void run(() => post(`/api/p/${name}/trash/restore`, { action_ids: [t.id] }), "Restore queued.")}>Restore</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Move an inbox into an archive</h2>
      <form className="inline" onSubmit={submitMove}>
        <label>
          Source (inbox)
          <select value={move.source_folder_id} onChange={(e) => setMove({ ...move, source_folder_id: e.target.value })} required>
            <option value="">choose</option>
            {inboxes.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
        <label>
          Target (archive)
          <select value={move.target_folder_id} onChange={(e) => setMove({ ...move, target_folder_id: e.target.value })} required>
            <option value="">choose</option>
            {archives.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
        <label>
          Directories (optional, one per line)
          <textarea value={move.directories} onChange={(e) => setMove({ ...move, directories: e.target.value })} rows={2} />
        </label>
        <button type="submit">Move</button>
      </form>

      <h2>Import an ImageStore (v1) database</h2>
      <form onSubmit={submitImport}>
        <label>
          Path of the v1 .db inside the container
          <input value={imp.db_path} onChange={(e) => setImp({ ...imp, db_path: e.target.value })} placeholder="/import/imagestore.db" required />
        </label>
        {imp.rows.map((row, i) => (
          <div className="inline" key={i}>
            <label>
              v1 Folder name
              <input value={row.name} onChange={(e) => setImp({ ...imp, rows: imp.rows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)) })} />
            </label>
            <label>
              Container path
              <input value={row.path} onChange={(e) => setImp({ ...imp, rows: imp.rows.map((r, j) => (j === i ? { ...r, path: e.target.value } : r)) })} placeholder="/media/images" />
            </label>
          </div>
        ))}
        <div className="actions-row">
          <button type="button" onClick={() => setImp({ ...imp, rows: [...imp.rows, { name: "", path: "" }] })}>Add row</button>
          <button type="submit" disabled={folders.length > 0}>Import</button>
          {folders.length > 0 && <span className="muted">Import needs a profile without Folders.</span>}
        </div>
      </form>

      {notice && <p className="muted" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 2: Verify and commit**

Run: `cd web && npm run lint && npm run build` — Expected: clean.

```bash
git add web/src
git commit -m "feat(web): jobs, settings, trash, archive move and import page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 23: End-to-end flow, documentation, self-review

**Files:**
- Modify: `web/e2e/start-server.sh`, `web/e2e/smoke.spec.ts`, `.gitignore` (`web/e2e/.media-path`), `docs/plans/README.md`, `README.md`

**Interfaces:**
- `start-server.sh` creates `$tmp/media/lib/{a.jpg,b.jpg}` with identical content and `$tmp/media/lib/sub/c.jpg` different, writes the library path to `web/e2e/.media-path`; the spec reads that file.
- The spec extends the milestone 1 flow: create Folder → Scan now → Groups page shows one open group of 2 within 30 s → open it → `Enter` confirms the preselection (first kept, second deleted) → Jobs page lists the trashed file → Restore → trash empty.

- [ ] **Step 1: Extend the server launcher and the spec**

`web/e2e/start-server.sh`: after `tmp="$(mktemp -d)"` add

```bash
mkdir -p "$tmp/media/lib/sub"
printf 'identical-bytes' > "$tmp/media/lib/a.jpg"
printf 'identical-bytes' > "$tmp/media/lib/b.jpg"
printf 'different' > "$tmp/media/lib/sub/c.jpg"
printf '%s' "$tmp/media/lib" > "$(dirname "$0")/.media-path"
```

Append to `web/e2e/smoke.spec.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";

test("scan a folder, resolve the identical group, restore from trash", async ({ page }) => {
  const mediaPath = readFileSync(join(__dirname, ".media-path"), "utf8");
  await page.goto("/login");
  await page.getByLabel("Password").fill("e2e-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await page.goto("/profiles");
  if (await page.getByText("No profiles yet.").isVisible().catch(() => false)) {
    await page.getByLabel("Name", { exact: true }).fill("demo");
    await page.getByRole("button", { name: "Create" }).click();
  }
  await page.goto("/p/demo/folders");
  await page.getByLabel("Name", { exact: true }).fill("Lib");
  await page.getByLabel("Container path").fill(mediaPath);
  await page.getByRole("button", { name: "Create Folder" }).click();
  await expect(page.getByRole("status")).toHaveText("Folder created.");
  await page.getByRole("button", { name: "Scan now" }).click();
  await expect(page.getByRole("status")).toHaveText("Scan queued.");

  await page.goto("/p/demo/groups");
  await expect(page.getByRole("link", { name: "2 files" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "2 files" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/p\/demo\/groups$/);

  await page.goto("/p/demo/jobs");
  await expect(page.locator("code", { hasText: "b.jpg" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Restore" }).first().click();
  await expect(page.getByText("Nothing in the trash.")).toBeVisible({ timeout: 20_000 });
});
```

Add `web/e2e/.media-path` to `.gitignore`.

Run: `cd web && npm run e2e` — Expected: 2 passed.

- [ ] **Step 2: Documentation**

- `docs/plans/README.md`: milestone 2 row → `[2026-11-06-m2-byte-identical.md](2026-11-06-m2-byte-identical.md) | shipped`.
- `README.md` status block: "milestone 2 complete: VisualStore replaces ImageStore's same-file workflow (Folders, scanning, SHA-1 groups, trash, archive move, v1 import). Visual similarity arrives in milestone 3."

- [ ] **Step 3: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && cd web && npm run lint && npm test && npm run build && npm run e2e`
Expected: everything green.

```bash
git add web/e2e .gitignore docs/plans/README.md README.md
git commit -m "test: end-to-end scan/resolve/restore flow; docs: milestone 2 status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

Spec coverage for milestone 2 ([11 §13 item 2](../design/11-roadmap.md#13-milestones)): Folder / Extension / IgnoredDirectory management (Tasks 1, 15, 19); scan with move detection (Tasks 2, 9); SHA-1 fingerprinting (Tasks 7, 8); sha1 Pairs (Task 8); grouping (Tasks 3, 10); default rules (Tasks 4, 10); group detail page without image comparison (Task 21); apply_action with trash (Tasks 11, 16, 22); archive_move (Tasks 12, 17, 22); v1 import (Tasks 13, 17, 22). Trash retention purge (§8.5) is Task 11 + the planner in Task 14; scheduled scans (§7.1 "定时") are Task 14.

Deferred on purpose: compare-scope filtering (not needed for sha1 pairs; milestone 3), bulk rules and band filters (milestone 4), move/rename actions in the UI (milestone 4; the action table and disk primitive exist), `maintenance` job (milestone 6), optional SimilarFile import (§15).

Rulings baked into this plan (also in ADR-0004): archive priority as a column; `file.name` with extension; result sinks on the loop thread; `regroup_queue` for dirty tracking; one group per file; restore keeps the old id and sha1; inbox-only identical groups are never auto-executed (design says "必须人看"); a component whose every edge is `related_keep` is `resolved`; `mtime = 0` means unknown and is reconciled without rehashing (needed by import).

Cross-task names checked: `files_db.relative_path/update_stat/files_by_state/reset_for_recompute/delete_files`, `groups_db.NewGroup/replace_groups/members/member_to_dict/mark_dirty/pop_dirty`, `pairs_db.ordered/upsert_sha1_pair/set_judgment/edges_touching`, `actions_db.record/list_trash/supersede/trash_older_than`, `IoContext.open_metadata`, `RESULT_SINKS`, `move_file/remove_empty_parents/trash_path`, `jobs_db.latest_with_param/has_active_with_param`, route paths used by `Folders.tsx`, `Groups.tsx`, `GroupDetail.tsx`, `Jobs.tsx` against Tasks 15–18.
