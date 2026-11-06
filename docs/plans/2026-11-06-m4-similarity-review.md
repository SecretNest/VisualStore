# Milestone 4 · Similarity review — Task-level plan

> **Resolution:** task-level (see the note at the top of the milestone 3 plan). Expand after milestone 3 ships: the comparison pane, band filters and bulk rules all consume milestone 3's distances, bands and thumbnails.

**Goal:** Resolving a similarity group becomes a three-keystroke job: the group detail page shows the selected member next to its nearest neighbour with overlay/blink comparison, the queue filters by band and Folder, and bulk rules apply the default resolution to every group matching a filter after a confirmation that states what will be deleted.

**Architecture:** Frontend-heavy. The backend adds a nearest-neighbour lookup per member (from `pair` rows inside the group), a `bulk-resolve` endpoint that previews then executes the default rules over a filter (reusing `domain/resolution.preselect` and `apply_action`), a file detail endpoint exposing candidate pairs beyond the grouping threshold, and `move`/`rename` actions. No new jobs types; `apply_action` gains `move` and `rename` items.

**Spec:** [06 §8.1 pages 3–4, §8.3 (full), §8.4, §8.5 move/rename](../design/06-review-ui.md), [05 §7.5 last paragraph (candidates visible on file detail)](../design/05-pipeline.md#75-grouping-regroup), [01 §2.2 bulk rules](../design/01-requirements.md), [11 §13 item 4](../design/11-roadmap.md#13-milestones).

## Inputs from earlier milestones

- Milestone 3: `pair.distance` per vector pair, `band()` and threshold settings, thumbnails endpoint, `GroupDetail.tsx` with keyboard model (`groupKeys.ts`).

## Decide before expanding

1. Comparison modes: side-by-side, overlay with opacity slider, blink (alternate at 2 Hz). Recommendation: implement all three; they are small once the two images are loaded.
2. Whether bulk execution runs synchronously (one `apply_action` per group) or as one batched job. Recommendation: one `bulk_resolve` io job that iterates groups and enqueues nothing else, so a single job row shows progress and the UI can poll it.
3. Image delivery for the pane: original via `/files/{id}/content` (full resolution, slow on SMB) vs a 1024 px "preview" generated on demand and cached next to thumbnails. Recommendation: preview endpoint with on-demand generation and a `previews/` cache that maintenance may clear.

## Global Constraints (additions)

- Bulk execution never deletes a file the default rules would leave `undecided`; groups that are not fully preselected are skipped and counted.
- Every bulk run shows the exact count of files to delete and the Folders affected before the user confirms; the confirmation token is the count typed back when it exceeds a setting `bulk.confirm_threshold` (default 100).
- Keyboard shortcuts from milestone 2 are kept; new keys: `o` overlay, `b` blink, `s` side-by-side, `n`/`p` next/previous neighbour, `Space` toggles comparison target.

## Review Focus

1. Bulk rule on a filter that matches zero fully-preselected groups must do nothing and say so, not silently "succeed".
2. A group that changes (regroup) between preview and execution must not have stale decisions applied: execution re-evaluates the rules and skips groups whose member set changed since preview.
3. `move`/`rename` to a path that exists (on disk or in the table) must fail that item without touching the disk, and a cross-Folder move must update `folder_id`, keep id/sha1/pairs, and mark dirty (role may change).
4. The comparison pane must handle a missing original (file deleted outside VisualStore) by showing the thumbnail and a notice, not a broken image.
5. Candidate pairs shown on the file detail page must be the ones beyond `group_distance` but within `candidate_distance`, never pairs judged `unrelated`.

## File structure

```
visualstore/
  api/groups.py                 + GET /groups/{id}/neighbours (per member: nearest other member with distance), band filter
  api/bulk.py                   POST /groups/bulk/preview {filter}, POST /groups/bulk/execute {filter, confirm}
  api/files.py                  + GET /files/{id}/preview (1024 px webp, cached), + candidates in detail
  api/actions.py                POST /files/{id}/move {folder_id, directory}, POST /files/{id}/rename {name}
  jobs/io/bulk_resolve.py       bulk_resolve job
  jobs/io/actions.py            + move/rename items in apply_action
  media/decode.py               + preview(frame, 1024)
  domain/resolution.py          unchanged, reused
web/src/pages/GroupDetail.tsx   comparison pane, neighbour navigation, thumbnails strip
web/src/components/Compare.tsx  side-by-side / overlay / blink
web/src/pages/Groups.tsx        band filter, bulk rule panel with preview and confirmation
web/src/pages/FileDetail.tsx    file info, group link, candidate pairs with thumbnails and judgment buttons
web/src/pages/groupKeys.ts      new keys
```

## Tasks

1. Neighbour lookup API and group list `band` filter (backend + tests: nearest member by min distance, ties by path).
2. Preview endpoint with cache (`previews/{id[:2]}/{id}.webp`), 404 → thumbnail fallback header; tests with generated images; maintenance hook noted for milestone 6.
3. `Compare.tsx` with the three modes and the key handling in `groupKeys.ts` (unit tests for the key model; component logic kept in pure helpers where possible).
4. Group detail integration: comparison pane, neighbour navigation, missing-original notice (Review Focus 4), thumbnails strip; Playwright flow: resolve a near-duplicate group with `d`, `Enter`.
5. File detail page and candidate pairs API (Review Focus 5) with judgment buttons that call the existing pair judgment endpoint.
6. `bulk_resolve` job: preview computation (pure function over groups + rules), execution with re-validation (Review Focus 1–2), progress, result summary; API preview/execute; tests over mixed groups.
7. Bulk UI on the queue page with confirmation and result display; setting `bulk.confirm_threshold`.
8. `move`/`rename` actions (Review Focus 3): disk-first via `move_file`, row update, dirty marks, action rows; API; UI buttons on file detail.
9. Docs: README review workflow section; plans README; design §8.3 cross-check (any divergence recorded as ADR-0006).

## Acceptance

- A near-duplicate group is resolved with three keystrokes in the browser; overlay and blink work on two real images.
- A bulk rule over "near_duplicate, contains archive file" resolves all matching fully-preselected groups in one job, after a confirmation showing the delete count.
- File detail shows candidates and lets the user judge them; `unrelated` splits groups live.
