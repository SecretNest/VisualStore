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

- **Cookie `Secure` flag**: VisualStore speaks plain HTTP behind the operator's TLS-terminating proxy (README "Behind a reverse proxy"); a `Secure` cookie would break the documented deployment. Not changed.
- **Concurrent-writer backup test**: already covered by `test_backup_taken_during_live_writes_is_complete_and_consistent` since milestone 1. Nothing to add.
