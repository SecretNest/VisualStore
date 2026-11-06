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

## Facts learned while shipping

- **Preview rendering.** libvips' shrink-on-load `thumbnail_source` ignores `fail_on` and renders a truncated PNG as a valid image, so `preview_webp` streams through the same `new_from_source(..., access="sequential", fail_on="truncated")` loader as fingerprinting and shrinks with `thumbnail_image`. Decoding happens at full resolution (bounded by `media.max_pixels`) before the shrink.
- **Design §8.3 cross-check.** The member list shows role, path, size, distance to the nearest member and the byte-identical tag; pixel dimensions are not stored on `file` yet (milestone 5 adds media metadata), and the video frame strip is milestone 5. Everything else in §8.3 is implemented; the group's inner pairs are no longer listed separately in the UI beyond their count, since the comparison pane and the candidates page cover the use.
- **Enter and the next group.** Confirming a group navigates to the next open group, not back to the queue; the e2e now reflects that.
