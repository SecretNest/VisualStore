# Settings

All settings are per profile, under **Jobs → Settings**. Changes take effect on the next job.

## Backup

| Key | Default | Effect |
|---|---|---|
| `backup.metadata.interval_hours` | 24 | How often `visualstore.db` is backed up. |
| `backup.metadata.keep` | 14 | Copies kept. |
| `backup.vectors.interval_hours` | 168 | How often `vectors.db` is backed up. |
| `backup.vectors.keep` | 4 | Copies kept. |

## Scan and trash

| Key | Default | Effect |
|---|---|---|
| `scan.interval_hours` | 24 | Scheduled scan of every enabled Folder; 0 disables. |
| `trash.enabled` | true | Deletion moves to the trash directory; off = permanent. |
| `trash.retention_days` | 30 | Days before trashed files are purged (daily job). |
| `resolution.auto_execute_identical` | true | Byte-identical groups are executed without confirmation. |

## Similarity and grouping

| Key | Default | Effect |
|---|---|---|
| `similarity.candidate_distance` | 0.5 | Largest cosine distance stored as a pair. |
| `similarity.near_duplicate` | 0.15 | Upper bound of the near-duplicate band. |
| `similarity.variant` | 0.3 | Upper bound of the variant band. |
| `similarity.top_k` | 64 | Neighbours fetched per frame before compare-scope filtering. |
| `grouping.group_distance` | 0.3 | Pairs up to this distance form groups. |
| `model.id` | `sscd-resnet50-512-v1` | Active model; change it with **Jobs → Model**, not here. |

The thresholds are provisional until you run the evaluation on your own judgments
([Import from ImageStore](import-from-imagestore.md)); the report suggests values.

## Media and frames

| Key | Default | Effect |
|---|---|---|
| `media.max_pixels` | 80 000 000 | Images and frames above this are marked unreadable. |
| `media.ffmpeg_timeout_s` | 120 | Timeout per ffmpeg/ffprobe call. |
| `frames.max_per_file` | 8 | Frames sampled per video or animated image (videos ≤ this many seconds get one per second; longer ones this many spread frames plus the first keyframe). |

## Index memory

| Key | Default | Effect |
|---|---|---|
| `index.quantization` | `f16` | `f16` (2 bytes per dimension) or `i8` (1 byte). |
| `index.pca_dims` | 0 | 0 = off; otherwise the index dimension after a per-profile PCA (for example 256). |
| `maintenance.vacuum_free_ratio` | 0.2 | The weekly maintenance job runs VACUUM when more than this share of database pages is free (and only while no other job is active). |
| `jobs.keep_days` | 7 | Done and cancelled jobs are forgotten after this many days (failed ones after four times as long). |

Changing an index option queues a full rebuild; stored vectors are untouched. Index size per
frame: dimension × bytes per dimension plus about 100 bytes of graph. For a million stills:
about 1 GB at `f16`/512, 0.5 GB at `i8`/512, 0.25 GB at `i8`/256. Measure recall for your own
vectors before choosing:

```bash
docker compose exec visualstore python scripts/measure_index.py \
  --vectors /data/profiles/<profile>/vectors.db --model sscd-resnet50-512-v1 --pca 256
```

## Bulk rules

| Key | Default | Effect |
|---|---|---|
| `bulk.confirm_threshold` | 100 | Above this many deletions a bulk run requires the count typed back. |

## Model

The image ships `sscd-resnet50-512-v1` (default) and `dinov2-vits14-384-v1`. To use your own
ONNX export, mount a directory as `VISUALSTORE_MODEL_DIR` holding every `.onnx` you need plus a
`models.json` describing custom ones (`{"<id>": {"dim", "input_size", "mean", "std", "description"}}`),
then switch with **Jobs → Model**.

## Sizing the compute pool

`VISUALSTORE_COMPUTE_PROCESSES` (environment, not a setting) is the number of parallel compute
children. Each runs one ONNX session (about 300 MB) and decodes one file at a time (frames of a
video are shrunk to 1024 px before embedding). On a NAS, two children usually keep up with daily
arrivals; on the initialisation machine use the CPU count. Watch **Jobs** for fingerprint
throughput and `docker stats` for memory.
