# 02 · Architecture and Technology Stack

## 3. Architecture overview

```
┌───────────────────────── one container (visualstore) ────────────────────────┐
│                                                                             │
│  browser ──HTTP──► server process (FastAPI + static frontend)               │
│                     │  API, scan, file actions, grouping, backup, scheduling│
│                     │                                                       │
│                     ├─► compute process pool (multiprocessing, N = CPUs)    │
│                     │    SHA-1, decode, frame sampling, model inference,    │
│                     │    thumbnails, index queries                          │
│                     ▼                                                       │
│   /data (volume)                              /media/... (volume)           │
│     global.db       session key, profile list   media directories per profile│
│     profiles/<name>/                                                        │
│       visualstore.db  metadata (SQLite)                                     │
│       vectors.db      vectors (SQLite)                                      │
│       index.usearch   derived index                                         │
│       thumbs/         thumbnails                                            │
│   /backups (volume)                                                         │
│     global.db, <name>/...                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

One image, one container, one entry point. Two kinds of executor inside:

- **Server process**: the only database writer, with every profile's databases open at once. Serves the UI and the REST API. Runs the I/O-bound jobs itself: scan, file actions (trash / move / rename), grouping, backup, trash purge, import.
- **Compute process pool**: child processes started by the server with `multiprocessing`, defaulting to the CPU count, configurable. Runs the CPU-bound jobs: fingerprinting (SHA-1, decoding, frame sampling, inference, thumbnails) and matching (index writes and nearest-neighbour queries). Children never touch the database. They receive jobs and return results over inter-process queues; the server writes to the database.

Compute runs in child processes rather than threads because inference and decoding saturate the CPU. Inside the server process they would slow web responses, and Python threads cannot run that work in parallel.

### 3.1 Initialisation and migration

The first full computation (inference over a million files) is heavy and is not done on the NAS:

1. Start the same container on a powerful machine (arm64 or amd64, the image is multi-arch). Mount the media directories over SMB at the **same container paths** as on the NAS (for example `/media/images`). Keep the data directory on a local disk.
   SMB options: mount with cifs on the host and bind-mount into the container, or use Docker's cifs volume driver (credentials then end up in the compose file). `/data` must be on a local disk; SQLite locking on network filesystems is unreliable. SMB offers no change notifications; the design never depends on them, scanning is scheduled or manual.
2. Create the profile, configure Folders, import v1, scan, wait for fingerprinting and matching to finish.
3. Stop the container and copy the whole `/data` directory to the NAS.
4. Start the container on the NAS with the same media mount paths. It works immediately. New arrivals are computed incrementally on the NAS at its own pace.

Portability of the data directory rests on three rules that the implementation must keep:

- Folder paths in the database are container paths, not host paths.
- `/data` uses only relative references internally and records no absolute host path or machine identity.
- Consistency between index files and model version is checked with `model_id`, not with machine identity.

---

## 4. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | The most complete ecosystem for models, decoding, and video; full arm64 support. |
| Web framework | FastAPI + uvicorn | Async API, OpenAPI built in. |
| Database | SQLite in WAL mode, SQLAlchemy Core or plain sqlite3 | A natural fit for the single-writer model. No ORM session semantics. |
| Migrations | Alembic or a hand-written version table | Versioned schema migration is mandatory. v1 lacked it and paid for it. |
| Job scheduling | Home-grown, table-driven, plus a `multiprocessing` pool | See [04 Jobs](04-jobs.md). No Celery / Redis, to avoid one more service. |
| Model inference | ONNX Runtime | CPU inference, official wheels for amd64 and arm64. Models are exported to ONNX and shipped in the image or downloaded on first start. |
| Image decoding | pyvips (libvips) | Fast, memory-frugal, supports jpeg / png / webp / gif / avif / heic / tiff. Must verify that the arm64 image's libvips has heif support compiled in. |
| Video frame extraction | ffmpeg subprocess | ffmpeg ships inside the image. |
| Vector index | usearch | HNSW with f16 / i8 quantisation and mmap read-only views, so memory is controllable. Fallback: hnswlib. |
| Frontend | React + TypeScript + Vite | Single-page app; the build output is baked into the image and served statically by the server. |
| Tests | pytest; vitest for the frontend; Playwright end to end | |
| Container | Multi-arch image (linux/amd64, linux/arm64), docker compose example | |
