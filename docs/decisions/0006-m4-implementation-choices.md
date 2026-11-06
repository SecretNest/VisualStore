# ADR-0006: Milestone 4 implementation choices

Date: 2026-11-06 · Status: accepted

## Context

The milestone 4 task-level plan listed decisions to take before expansion.

## Decisions

- **Comparison modes**: side-by-side, overlay with an opacity slider, and blink (two images alternating every 500 ms). All three are small once both images are loaded; the key model (`groupKeys.ts`) carries the mode so it is testable without React.
- **Nearest neighbour travels with the group detail** instead of a separate endpoint: it is derived from the inner pairs the detail already loads, and one request keeps the page simple.
- **Preview images** are 1024 px webp files rendered on demand by the server with pyvips (`thumbnail_source`, shrink-on-load, same `Source`-based loading and `fail_on="truncated"` as fingerprinting) and cached under `previews/{id[:2]}/{id}.webp` next to `thumbs/`. The fingerprint sink deletes the cached preview when it writes a new thumbnail, so a changed file never shows a stale preview. Maintenance (milestone 6) may clear the directory at any time. The original stays available through `/content`.
- **Bulk rules** run as one `bulk_resolve` io job. The preview is a synchronous API call over the queue filter that applies the default rules to every open group and returns the planned groups, the skipped count, the number of files to delete, the Folders affected and a token (hash of the planned group ids and their member sets). Execution requires the same token (409 when the queue changed), the typed count when it exceeds `bulk.confirm_threshold`, and then the job re-validates each group (member set and recomputed rules) and trashes inline through the `apply_action` core, so one job row shows the progress and nothing else is enqueued.
- **Move and rename** are `apply_action` items (`move`, `rename`) rather than new job types; they never overwrite: an existing target on disk or in the table fails that item before the disk is touched.
- **Group resolution logic** (store decisions, mark kept pairs `related_keep`, trash or resolve) lives in `visualstore/jobs/resolve.py`, shared by the endpoint and the bulk job.

## Consequences

- `previews/` is derived data like `thumbs/`; backups do not include it.
- A bulk run over thousands of groups is one job; cancelling it (queued only) is cheap, interrupting it mid-way leaves already-processed groups resolved, which is correct since each group is committed independently.
