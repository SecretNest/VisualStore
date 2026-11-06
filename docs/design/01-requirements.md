# 01 · Requirements Baseline

## 2.1 Inherited from v1

| v1 concept | VisualStore treatment |
|---|---|
| Folder with three-level CompareImageWith and IsSealed | Kept. IsSealed becomes an explicit Folder role: archive / inbox. Compare scope stays an independent attribute. |
| IgnoredDirectory, optionally recursive | Kept. |
| Extension whitelist with IsImage flag | Becomes a media-type classification: image / animated / video / other. The extension table is still user-maintained, with common formats built in. |
| File: relative path, size, SHA-1, pHash, state, compared threshold | Relative-path model kept. pHash is dropped after import and never computed again. "Compared threshold" is replaced by match job state. |
| Sync-ImageStoreFolder | Scan job. Adds move / rename detection. |
| Measure-ImageStoreFiles, multi-threaded, recompute by state | Fingerprint job, executed by the compute process pool. |
| Compare-ImageStoreSameFiles | Byte-identical files are similarity pairs at distance 0, merged into the match flow. |
| Compare-ImageStoreSimilarFiles, full pairwise comparison | Vector index nearest-neighbour search, incremental. |
| Auto-selection rules of Select-ImageStoreSameFile | Generalised into the default resolution rules for every duplicate group (see [06 §8.2](06-review-ui.md#82-default-resolution-rules)). |
| Resolve-ImageStoreSimilarFiles grouping, per-file / per-relation views, three-state marking | Grouping and three-state judgment kept (effective / related but keep / unrelated). UI rebuilt. |
| Remove / Move / Rename / RemoveDirectory / ClearEmptyFolders | Resolution actions. Deletion goes to a trash directory by default. |
| Thumbprint cache | Thumbnails are generated during fingerprinting and persisted; no longer optional. |
| Compress-ImageStoreDatabase | VACUUM inside the maintenance job. |

## 2.2 New requirements

- Browser UI with single-password login.
- Job queue with visible background progress.
- Video and animated images contribute sampled frames to similarity and can match still images.
- Trash directory with retention period; deletion is reversible.
- Bulk rules: apply the default resolution to every group matching a filter (distance band, Folder) in one action.
- Scheduled backups, keeping the most recent N copies.
- v1 database import.
- Multiple profiles, switchable in the UI, fully isolated data.
- Portable data directory.
