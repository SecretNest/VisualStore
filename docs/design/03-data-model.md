# 03 · Domain Model and Database

## 5.1 Entities

**Profile**
The boundary of data isolation. Each profile owns `visualstore.db`, `vectors.db`, `index.usearch`, `pca.npz`, `thumbs/`, `previews/` and `reports/` under `/data/profiles/<name>/`. Every entity below (Folder, File, Pair, Group, Job, Action, Setting, ...) belongs to a profile and lives in that profile's database; tables need no `profile_id` column.
The profile list (`name`, `display_name`, `created_at`, sort order) lives in the global `/data/global.db`, the only cross-profile data. `name` doubles as directory name and URL segment and is limited to lowercase letters, digits, and hyphens.
Deleting a profile deletes its whole directory; the UI requires typing the profile name to confirm.

**Folder**
One media library root. Fields: `id`, `name`, `path` (container path), `role` (`archive` storage area / `inbox` new-arrivals area), `compare_scope` (`all` / `other_directories` / `other_folders`), `enabled`.
`role` drives the default resolution bias and the archive-move direction; `compare_scope` drives exclusion during matching. The two are independent.
Folders must not nest; use IgnoredDirectory when nesting would otherwise be needed.

**IgnoredDirectory**
`folder_id`, `directory` (relative path), `recursive`.

**Extension**
`extension` (lowercase, no dot), `media_type` (`image` / `animated` / `video` / `other`), `enabled`.
`other` gets SHA-1 only. Built-in defaults: jpg jpeg png webp avif heic heif bmp tiff → image; gif apng → animated (webp is classified by content); mp4 mkv webm mov avi → video.

**File**
`id`, `folder_id`, `directory` (relative path, `""` for the root), `name`, `extension_id`, `size`, `mtime`, `sha1`, `state`, `created_at`.
`state`: `new` (just scanned) / `fingerprinted` / `unreadable` / `not_media`. (`missing` is reserved and unused: a scan deletes the rows of vanished files directly, with their derived images.)
Unique index on `(folder_id, directory, name)`, case-insensitive (SQLite `COLLATE NOCASE`, ASCII folding only; this defines path comparison).

**Fingerprint** (in `vectors.db`)
`file_id`, `frame_index`, `frame_time_ms`, `vector` (f16 BLOB, dimension set by the model, initially 512), `model_id`.
One row for a still image; several rows for animated images and video (see [05 §7.3](05-pipeline.md#73-frame-sampling)). `model_id` lets vectors from two models coexist during a model change and identifies which files need recomputing.

**Pair**
`file_a`, `file_b` (`file_a < file_b`, guaranteeing uniqueness), `distance` (0 to 1, cosine distance; 0 for byte-identical; nullable, since pairs imported from v1 have no distance until the new model computes one, and null-distance pairs take no part in grouping), `source` (`sha1` / `vector` / `import`), `judgment` (`none` / `related_keep` / `unrelated`), `judged_at`, `judgment_origin` (`user` / `import_v1` / `rule`), `model_id`.
`related_keep` corresponds to v1's HiddenButConnected: related, no further action, still part of the connected group.
`unrelated` corresponds to v1's Disconnected: not related, excluded from grouping.
Pairs imported from v1 carry `judgment_origin = import_v1`; the review queue identifies them by this field.

**Group** (derived, recomputable)
`id`, `member_count`, `min_distance`, `has_identical`, `status` (`open` / `auto_resolved` / `resolved` / `dismissed`), `updated_at`.
`GroupMember`: `group_id`, `file_id`.
Groups are connected components over Pairs, see [05 §7.5](05-pipeline.md#75-grouping-regroup).

**Job**
`id`, `type`, `params` (JSON), `state` (`queued` / `running` / `done` / `failed` / `cancelled`), `priority`, `kind` (`io` / `compute`), `attempts`, `progress` (JSON: done / total / message, plus a compact `result` once the job is done), `error`, `created_at`, `started_at`, `finished_at`.

**Action** (audit and undo)
`id`, `type` (`trash` / `restore` / `move` / `rename` / `purge`), `file_id`, `before` (JSON), `after` (JSON), `at`.
Files in the trash directory are restored to their original location through this table.

**Setting**
Key-value table, per profile: backup policy, threshold bands, trash retention, model id, frame-sampling parameters, archive Folder priority order, and so on. The password is not here, see [07 §9.2](07-operations.md#92-authentication).

## 5.2 Database files

One global file, two per profile:

- `/data/global.db`: Profile table, session signing key.
- `/data/profiles/<name>/visualstore.db`: every table of the profile except Fingerprint. A few hundred MB at the one-million-file scale.
- `/data/profiles/<name>/vectors.db`: the Fingerprint table. One million files, 1 to 8 frames each, 512-dim f16: roughly 1 to 8 GB.

Why split metadata from vectors: the metadata database is small, so frequent backups are cheap; the vector database is large and changes slowly, so it can be backed up less often. Both are sources of truth. `index.usearch` is derived and can be rebuilt from `vectors.db` if lost.
Why one database set per profile: isolation is physical. Backup, restore, delete, and import of one profile are operations on one directory and cannot damage another.

## 5.3 Invariants

- All primary keys are UUIDs stored as 16-byte BLOBs, generated in the application so v1 ids can be preserved on import.
- `PRAGMA foreign_keys = ON`, `journal_mode = WAL`, `synchronous = NORMAL`.
- Deleting a File cascades to its Pairs, GroupMembers, Fingerprints, and thumbnail; the Group is marked for recomputation.
- Only the server process opens database connections. Compute children never do.
- No host path ever appears inside `/data`.
