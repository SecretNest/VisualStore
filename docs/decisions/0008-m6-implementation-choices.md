# ADR-0008: Milestone 6 implementation choices

Date: 2026-11-07 · Status: accepted

## Context

The milestone 6 task-level plan listed decisions to take before expansion: index quantisation default, PCA strategy, and the non-root container user.

## Decisions

- **Quantisation is a setting, default `f16`.** `index.quantization` accepts `f16` (2 bytes per dimension, exact enough for the bands) and `i8` (1 byte, for tight memory). `scripts/measure_index.py` reports size and recall for a profile's own vectors so the operator can decide with numbers; the default stays `f16` until a measurement on real data says otherwise.
- **PCA is per profile, trained at rebuild time, off by default.** `index.pca_dims` (0 = off) makes `rebuild_index` fit a projection on a sample of up to 100 000 of the profile's vectors (numpy SVD), save it as `pca.npz` beside the index, and build the index in the reduced space; `match` projects new vectors and queries with the same file. Vectors in `vectors.db` are never changed, so switching PCA off or changing the dimension is just another rebuild. A fixed projection per model would need shipping and versioning more artefacts for a feature few installations will turn on.
- **Changing either option enqueues a rebuild from the settings endpoint**; start-up also compares `index_meta` with the settings and rebuilds when they differ (so a restored database with other settings heals itself).
- **Non-root container with `PUID`/`PGID`.** The image starts as root in `docker/entrypoint.sh`, which creates the target user if needed, changes the ownership of `/data` and `/backups` only when they are empty (an existing data directory is never chowned: it may be on a volume the operator manages), and drops privileges with `setpriv`. A Python pre-flight then verifies both directories are writable and exits with a clear message otherwise, before the scheduler starts.
- **Health = HTTP + scheduler heartbeat.** `/api/system/health` answers 503 when the scheduler has not ticked for 60 s; the Dockerfile HEALTHCHECK probes it with Python's urllib (no curl in the image).
- **Maintenance is a weekly io job** at maintenance priority: orphan thumbnails/previews/frame images, frame rows of unknown files, ANALYZE, and VACUUM only above a free-page ratio.
- **Retry backoff**: a failed attempt re-queues the job with `not_before = now + 30 s × attempts`; the picker skips jobs whose `not_before` is in the future.

## Consequences

- `pca.npz` and `index.usearch` are derived data like thumbnails; backups skip them.
- The measurement script and the performance numbers on the user's library are operator steps; the design records where the numbers go.

## Deferred minors retired

- **Cookie `Secure` flag**: VisualStore speaks plain HTTP behind the operator's TLS-terminating proxy (user guide, [deployment: reverse proxy and HTTPS](../user/deployment.md#reverse-proxy-and-https)); a `Secure` cookie would break the documented deployment. Not changed.
- **Concurrent-writer backup test**: already covered by `test_backup_taken_during_live_writes_is_complete_and_consistent` since milestone 1. Nothing to add.

## Polish pass after the audit (2026-11-07)

The deferred minors of the audit were worked through on the `polish` branch (version 0.2.0). Behaviour changes: logout invalidates every session issued before it (a per-registry stamp, so a replayed cookie fails); a profile with running jobs refuses deletion; derived images (thumbnail, preview, frames) go with the file on trash, scan deletion and Folder deletion instead of waiting for the weekly sweep; a `clean_empty_dirs` job; file dimensions stored at fingerprint time and shown in the review UI; the overview flags Folders whose path is not a directory; the Jobs page polls only the job list; component tests run under jsdom next to the pure-function tests. Retired without change: a static ffmpeg build (the Debian package is enough and tracks security updates), the "APNG decoded twice" note (pages are decoded once, dimensions come from the probe), the positional PNG zip in the animated loader (rewritten with the delays), and re-hashing on v1 import (the import keeps v1's SHA-1 and the first scan verifies it).

## Facts learned while shipping

- usearch reports the scalar kind in `Index.metadata()["kind_scalar"]` as a `ScalarKind` enum; `inspect()` maps it to the setting names (`f16`, `i8`).
- The design's job table listed `rebuild_index` and `eval` as compute jobs; both are io jobs (the rebuild exports and queues the compute `build_index`), and `build_index`, `model_switch` and `bulk_resolve` were missing. The table was corrected in place.
- `ffprobe` has no `-nostdin`; the e2e job on GitHub runners needs `libvips42` and `ffmpeg` from apt and Chromium from `playwright install --with-deps`.
- A restored metadata database still contains the job rows of its time; the restore rehearsal therefore distinguishes jobs created after the restore by id.
- The retry backoff would stall tests that wait for a third failed attempt; the integration and scheduler test suites zero `RETRY_BACKOFF_S` through an autouse fixture.
- Performance numbers for the one-million-file acceptance criterion cannot be produced in this repository; the user guide explains how to size the pool and where the numbers are recorded (design §1.3).

## End-of-project audit (2026-11-07)

Two fresh auditors reviewed the whole codebase after milestone 6 (backend core; operations, frontend, docs and tests). Behaviour changed by the fix pass:

- **Job table hygiene.** Compute results are compacted to counts when a job finishes (no vectors, thumbnails or hit lists are stored), a finished `match` job keeps only the number of its entries, and the weekly maintenance job forgets done/cancelled jobs older than `jobs.keep_days` (default 7) and failed ones older than four times that.
- **Imported files get vectors.** A scan queues fingerprinting for every fingerprinted media file that lacks vectors for the active model (v1 imports, restores), not only for new files; files already named by a queued job are never queued twice, and a manual scan lifts the priority of jobs that hold its files.
- **Scan safety.** A subtree that fails to list is not an empty subtree: when any directory could not be read, the scan records nothing as missing (`listing_errors` in the result). Names that are not valid UTF-8 are skipped and counted. A file vanishing between listing and stat is skipped.
- **Only operator job types** (`backup`, `scan`, `regroup`, `purge_trash`, `maintenance`, `rebuild_index`, `ping`) can be created through `POST /jobs`; compute jobs and file actions carry paths and are created by the server only. `model.id` is not settable through the settings endpoint (use the model switch); numeric settings have sanity maxima.
- **Regroup.** Dirty marks are cleared only once the groups are written; every `IN (...)` query is chunked, so a bucket of tens of thousands of identical files regroups; a group whose member set did not change keeps its id and, when still open, its stored decisions, so a confirmation racing a regroup is not dropped.
- **Scheduler.** Messages are drained before dead children are matched, and a death whose job has not announced its pid yet is matched on the next tick. Queued `match` jobs for the same index absorb new entries (up to 4096) so a backlog rewrites the index once per drain, not once per 64 vectors.
- **Folders.** Deleting a Folder disables it at once and removes its files, vectors, index keys and derived images in a `folder_delete` job (`DELETE` answers 202 with the job); changing a Folder's role or priority marks its files dirty. Restoring a trashed video or animated image queues its vectors like an image. Bulk planning runs in a thread on its own connection.
- **Review UI.** `Enter`/`Esc` follow `GET /groups/{id}/next` (queue order, wrap-around, skipping groups whose decisions are being executed); marking a pair unrelated waits for the regroup before moving on; a failed confirmation keeps the page; polling no longer discards "Load more"; the Overview shows per-Folder state counts and groups by status; `index.quantization` is a select and a queued rebuild is announced; done jobs expose their result and a job in backoff shows when it retries.
- **Tests and CI.** `VISUALSTORE_TEST_REQUIRE_MEDIA=1` (set in CI and in the test image) turns a missing libvips/ffmpeg into a failure instead of a skip; a two-profile isolation test covers design §10's bullet.

Not changed, by decision: sessions stay bearer tokens until expiry (logout clears the cookie only); thumbnails of trashed files are swept weekly rather than on deletion; the `missing` file state of design §5.1 is not materialised (the scan deletes rows directly, as ADR-0004 decided); `IgnoredDirectory` ids are regenerated on import; rebuild memory (vectors held twice during export) is accepted for the NAS-side restore case.

## Second audit (2026-11-07, version 0.2.1)

Three fresh auditors (backend core; frontend and API contract; operations, docs, tests and CI)
reviewed `main` at 0.2.0. Everything they rated Important was fixed the same day; the retest
of the index uncovered one more defect.

**Backend.** The rebuild export is a snapshot up to the highest live key at its start; keys the
fingerprint sink writes meanwhile are left to `match_unindexed` instead of entering the export as
zero vectors. The dirty queue carries a generation (profile migration 0005): a mark set while a
regroup batch runs survives that batch, so a judgment made during the regroup is applied by the
next pass. A file the compute child cannot open (`OSError`, including not-found) is reported as
`retry` and the sink leaves its row alone; the next scan decides, instead of a permanent
`missing`/`unreadable` without hash. An interrupted v1 import undoes its Folders on the retried
attempt and starts over; a finished import re-run by hand is still refused. `match`,
`build_index`, `apply_action`, `bulk_resolve`, `folder_delete` and `model_switch` cannot be
cancelled through the API, and the start-up check queues a rebuild when the index holds fewer
keys than the vector database with no match job pending (also the case of a `vectors.db`
restored older than the index). `purge_trash` continues past a file it cannot remove and
reports the failures at the end. Changing `grouping.group_distance` marks every paired file
dirty, so groups are re-formed at the new distance. The two remaining unchunked `IN` clauses
are chunked; `plan_regroup` does not re-enqueue while the latest regroup is failed (the
operator's retry lifts the hold); a raise while finishing a job records the failure instead of
leaving the job `running`; a model switch re-queues files still `new`; dismissing a group keeps
an explicit `unrelated` judgment; float settings accept whole numbers (JSON has one number
type).

**usearch add is single-threaded.** While retesting the i8 rebuild, the neighbour test failed on
unmodified code: with usearch 2.26.6, a multi-threaded `Index.add` builds a graph that misses
about one exact near-duplicate in a few hundred queries (measured with 60–3000 clustered
vectors, bulk and incremental, f16 and i8 alike); `threads=1` is exact and about 1.5× slower.
`VectorIndex.add` passes `threads=1`; recall is the product, and the build runs in a compute
child. `test_i8_rebuild_returns_the_same_nearest_neighbours_as_f16` is the regression test.

**Frontend.** The review-key handler leaves a focused button or link its own Enter/Space and
ignores Ctrl/Cmd/Alt chords; the previous group's members are cleared when the URL changes and a
response for an earlier group is dropped (numbered requests). Archive move checks for open
groups of the source Folder before queuing and reports the job's `skipped` note; file actions
and the archive move wait for their job and show its outcome. "Load more" stops when a page is
short and reports errors; destructive buttons are disabled while their request runs; the
overview keeps its data through a poll hiccup; thumbnails retry three times while a scan is
still producing them; validation errors render their messages; "Mark unrelated" is disabled
when the two files have no pair of their own; byte-identical members carry a tag. Component
tests under jsdom now cover the group-detail keyboard handling and navigation.

**Operations.** `__version__` comes from the installed distribution (one source: `pyproject.toml`)
with a unit test and a CI check that the image reports the pyproject version — the 0.2.0 image
had announced itself as 0.1.0. The publish workflow refuses a typed version that differs from
`pyproject.toml` unless forced, makes moving `latest` an input, and serialises dispatches. CI
builds the test stage and exercises the entrypoint (PUID/PGID, `HOME`); it has read-only token
permissions and cancels superseded runs. The compose example sets `init: true` and
`stop_grace_period: 2m`; a backup removes `.tmp` copies a killed run left behind; `.env` and test
artefacts are excluded from the build context. Documentation drift (project layout, backup
scope including `pca.npz`, `previews/` and `reports/`, the restore guide, the deployment and
testing chapters, the import guide's sentence on thresholds) was corrected.

**Test suite.** The two rehearsal tests still selected the tiny model through `PUT
settings/model.id`, which the first audit had turned into a 400: they had silently been running
against the real SSCD model, downloading it from GitHub on every run (40–60 s, flaky under load).
They now switch through `/model-switch` and take under ten seconds offline.

**Noted, not changed.** Restoring a trashed file does not bring its pair judgments back (the
pair rows are recreated by the next match with judgment `none`); a trash action would have to
snapshot them. Every `match` job loads the whole index into the compute child and rewrites the
file; whether that fits design §9.5 at a million files is the operator's measurement. A long
`VACUUM` or import rollback on the io thread can hold the loop's SQLite writes past the busy
timeout; `VACUUM` runs only when no other job is active. The runtime image still ships `uv` and a
second copy of the package tree.
