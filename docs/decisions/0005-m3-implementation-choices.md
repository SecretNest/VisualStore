# ADR-0005 · Milestone 3 implementation choices

Date: 2026-11-06
Status: accepted

## Context

The milestone 3 task-level plan listed decisions to take before expansion. They are taken here, together with facts learned while probing the toolchain.

## Decisions

- **Model artefacts are downloaded on first use** into `VISUALSTORE_MODEL_DIR` (default `/data/models`) and verified against a sha256 pinned in `visualstore/models/models.json`. The image does not embed weights. An entry whose sha256 is empty is "local only": the server refuses to download it and accepts only a file already present in the model directory. ONNX files are produced once by `scripts/export_models.py` (torch + torchvision, not project dependencies) and published as assets of a GitHub release of this repository; the registry points at those URLs.
- **usearch keys are plain `index_key.seq` integers** issued by the server (never recycled). The task-level plan proposed encoding a directory hash into the key so the child could filter same-directory neighbours during the search; usearch's Python filtered-search API is not relied upon. Instead the child over-fetches (`similarity.top_k`, default 64, as the design's fallback) and the result sink applies compare scope. If same-directory crowding shows up in practice, raise `top_k` or revisit filtered search in milestone 6.
- **Compare scope is applied in the match result sink**, server side, exactly as design §7.4 step 4 states; pairs that fail it are not stored.
- **Thresholds are provisional** (`candidate 0.5`, `near_duplicate 0.15`, `variant 0.3`, `group_distance 0.3`) until the evaluation report; the measured values go into design §1.3.
- **Candidate models**: SSCD ResNet-50 (512-d, 288 px input) first; DINOv2 ViT-S/14 (384-d, 224 px) as the comparison. Both exported to ONNX with dynamic batch size.
- **`eval` is an io job**, not a compute job as §6.2 lists: it reads the v1 database and `vectors.db` and does a few hundred thousand dot products with numpy, well under a minute; running it in a child would only add an export step.
- **Index removals are deferred**: deleting a file marks its `index_key` rows `deleted`; the next `match` job removes those keys from the index file and the sink drops the rows. Hits on not-yet-removed keys resolve to nothing and are ignored.
- **Decode tests need libvips**: they `importorskip("pyvips")`. The development machine used for milestone 3 has no libvips, so these tests run in CI (which installs `libvips42` and `ffmpeg`) and inside the image's `test` stage. Inference tests use a tiny ONNX model built with the `onnx` package; the real model is exercised only when `VISUALSTORE_TEST_REAL_MODEL=1`.
- **Frames for deleted files** are removed explicitly by the scan and trash paths through `IoContext.open_vectors()` (no cross-database foreign keys); milestone 6's maintenance job sweeps orphans.

## Consequences

- Milestone 5 adds frames per file without schema changes.
- A model switch is a setting change plus a `model_switch` job; old vectors stay until cleanup.
- Anyone running without internet access mounts a model directory and sets `VISUALSTORE_MODEL_DIR`.
