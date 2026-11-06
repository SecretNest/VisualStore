# 04 · Job System

## 6.1 Model

A table-driven queue with no external message broker. The Job table lives in each profile's own database. A scheduler coroutine inside the server polls every profile's queue and picks jobs by priority (ties broken by creation time), so profiles share compute fairly:

- The user or a timer inserts a Job with `state = queued`.
- Jobs with `kind = io` are taken by the server's own executor coroutine.
- Jobs with `kind = compute` are handed by the scheduler to the process pool's input queue. A child picks one up, reports progress and results over the output queue, and the server persists them and marks the Job `done`.

On container restart every `running` job returns to `queued` with `attempts` incremented; after three failures it becomes `failed`, visible in the UI with a manual retry. Compute jobs are idempotent (recomputing the same batch yields the same result), so rerunning is safe.

## 6.2 Job types

| type | kind | Description |
|---|---|---|
| `scan` | io | Scan one or all Folders, see [05 §7.1](05-pipeline.md#71-scan). |
| `fingerprint` | compute | SHA-1, thumbnail, and vectors for a batch of files (default 64). Result per file: sha1, state, frame-vector array, thumbnail bytes. |
| `match` | compute | Add a batch of new vectors to the index and query neighbours; returns candidate Pairs (see [05 §7.4](05-pipeline.md#74-match)). |
| `rebuild_index` | io | Export the live vectors of `vectors.db` (fitting the PCA projection when enabled) and queue `build_index`. Used after a model change, an index option change, a restore, or index corruption. |
| `build_index` | compute | Build a fresh index file from the exported vectors and replace the profile's; its sink matches whatever was fingerprinted meanwhile. |
| `model_switch` | io | Change the active model: cancel queued jobs of the old one, queue vectors for files lacking them, rebuild the index. |
| `bulk_resolve` | io | Apply the default rules to the groups a bulk preview planned, re-validating each group, trashing inline. |
| `regroup` | io | Incrementally recompute affected connected components. |
| `apply_action` | io | Trash / move / rename / restore for a batch of files. |
| `archive_move` | io | Move all (or selected directories) of an inbox Folder into a target archive Folder, see [06 §8.5](06-review-ui.md#85-actions-and-trash). |
| `backup` | io | See [07 §9.1](07-operations.md#91-backup). |
| `purge_trash` | io | Remove trashed files past the retention period. |
| `import_v1` | io | See [08 v1 Import](08-v1-import.md). |
| `eval` | io | Open a v1 database read-only and evaluate models / thresholds against its human judgments, see [05 §7.6](05-pipeline.md#76-model-and-thresholds). |
| `maintenance` | io | Weekly: orphan thumbnails / previews / frame images, frame rows of unknown files, ANALYZE, VACUUM above a free-page ratio. |

## 6.3 Priority

Default order: user-triggered > files in inbox Folders > files in archive Folders > maintenance. After a v1 import, vector computation for old files is the lowest-priority long tail; new arrivals always jump the queue.

## 6.4 Concurrency

- The server's file-action jobs run serially (one executor coroutine) so moves and scans never trample each other.
- Scan and fingerprint may run in parallel. Scan writes only File; fingerprint writes only Fingerprint and File.sha1 / File.state.
- `fingerprint` jobs may run on several children at once; they are independent.
- Within one profile only one `match` job runs at a time (the index has a single writer). Indexes of different profiles are unrelated and may run in parallel.
- Pool size is a global setting (environment variable `VISUALSTORE_COMPUTE_PROCESSES`, default CPU count). On the NAS it can be lowered to leave CPU for other services.
