# ADR-0004 · Milestone 2 implementation choices

Date: 2026-11-06
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
- **Unknown mtime**: imported files carry `mtime = 0`; the scan treats an equal-size file with unknown mtime as unchanged and records the real mtime, so an import followed by a scan does not discard every imported hash.
- **Inbox-only identical groups are never auto-executed** (design §8.2 says a human must look); archive-only single-Folder groups are undecided; a component whose every edge is `related_keep` is `resolved` with nothing to do.
- **Automatic execution and judgments** (final review): a byte-identical group that spans several archive Folders is executed by priority as §8.2 says (the plan's Review Focus 4 was worded too broadly and has been corrected); any group containing a human judgment is never executed automatically, and a `related_keep` edge propagates "keep" to both of its ends before preselection. Byte-identical edges are derived from sha1 equality during regroup (stored star pairs are a cache that regroup rebuilds), so deleting the hub of a star does not lose the identity of the remaining copies.
- **Scan safety** (final review): a scan whose Folder root is unavailable fails instead of deleting every row, and a root that lists no media while rows exist is refused ("is the share mounted?"). Case-only renames update the stored spelling; two names that collide under NOCASE are counted and skipped.
- **Stale results** (final review): a fingerprint result is ignored when the file's size or mtime no longer match the row; `apply_action` re-checks size/mtime and the group decision before trashing.
- **SimilarFile import stays off**; the switch is milestone 3+ work ([11 §15](../design/11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)).

## Consequences

- Milestone 3's fingerprint handler extends the same `fingerprint` job type (sha1 → sha1 + vectors + thumbnail); its result sink grows accordingly.
- The compare-scope filter is not needed for sha1 pairs (design §7.4: byte-identical pairs ignore scope) and is therefore not implemented in milestone 2.
