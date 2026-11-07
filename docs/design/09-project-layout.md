# 09 · Project Layout

```
visualstore/                   Python package
  app.py                       FastAPI assembly, process pool start-up, scheduler
  api/                         REST routes: auth, profiles, folders, files, groups, pairs, jobs, settings
  profiles.py                  profile registry; per-profile connection and path resolution
  db/                          connections, migrations, per-table query functions; migrations run per profile database
  domain/                      pure logic: grouping (union-find), default resolution rules, compare-scope filter, threshold bands
  jobs/
    scheduler.py               polls every profile queue, dispatches io / compute jobs
    io/                        scan, actions, regroup, backup, purge, import_v1, eval, rebuild, model_switch, bulk_resolve, folder_delete, clean_empty_dirs, maintenance
    compute/                   child-process entry point; fingerprint, match, build_index, ping
  media/                       decoding, frame sampling, video/animated frames, thumbnails (called by compute, testable alone)
  index/                       usearch wrapper and the optional per-profile PCA projection
  preflight.py                 start-up check that the data and backup directories are writable
  models/                      ONNX loading and preprocessing
  static/                      frontend build output
web/                           React frontend source
  e2e/                         Playwright smoke test and its server script
scripts/                       model export (needs torch, not shipped) and index memory measurement
tests/
  unit/                        domain, db, media
  integration/                 full pipeline on a temp directory with small images
docker/                        Dockerfile, entrypoint (PUID/PGID), compose example, media-support check
docs/                          design, decisions, plans, user documentation (see docs/README.md)
```

Logic in `domain/` depends on neither the database nor I/O; it is the bulk of the test surface. `media/` and `models/` do not depend on the job system, so evaluation scripts can run them standalone.
