# ADR-0005 · Milestone 3 implementation choices

Date: 2026-11-06
Status: accepted

## Context

The milestone 3 task-level plan listed decisions to take before expansion. They are taken here, together with facts learned while probing the toolchain.

## Decisions

- **Model artefacts are downloaded on first use** into `VISUALSTORE_MODEL_DIR` (default `/data/models`) and verified against a sha256 pinned in `visualstore/models/models.json`. The image does not embed weights. An entry whose sha256 is empty is "local only": the server refuses to download it and accepts only a file already present in the model directory. A `models.json` inside `VISUALSTORE_MODEL_DIR` adds local-only entries (`{"<id>": {"dim", "input_size", "mean", "std", "description"}}` next to `<id>.onnx`) and overrides built-in entries of the same id; this is how an operator brings a custom export and how the end-to-end test runs with a tiny model. The download happens in the `scan` io job before anything is recorded, so a profile whose model cannot be obtained fails its scan with a clear message instead of failing every fingerprint job. ONNX files are produced once by `scripts/export_models.py` (torch + torchvision, not project dependencies) and published as assets of a GitHub release of this repository; the registry points at those URLs.
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

## Facts learned while shipping

- **Export path.** Both models exported with the legacy TorchScript ONNX exporter (`torch.onnx.export(..., dynamo=False)`, opset 17, torch 2.9 CPU wheels); the SSCD TorchScript checkpoint loads with `torch.jit.load` and matches the ONNX output to 2.7e-7. DINOv2 loads through `torch.hub` without xformers (the warnings are harmless).
- **Import order.** On the runtime image (Debian bookworm, usearch 2.26, onnxruntime 1.31) importing onnxruntime *after* usearch's extension module segfaults the process; `visualstore.index.usearch_index` therefore imports onnxruntime first, and `tests/unit/test_import_order.py` guards it. The order is harmless on hosts where the crash does not occur.
- **Synthetic images are poor evaluation material.** Smooth gradients and Perlin noise collapse to near-identical SSCD embeddings regardless of content; the real-model smoke test draws coloured shapes on a gradient instead. Thresholds are still to be measured on the user's library through the evaluation job.

## Changes from the final review

- **Reused keys.** A re-fingerprinted file keeps its `index_key.seq`; `VectorIndex.add` now replaces a key that is already present instead of letting usearch reject the duplicate (which failed the whole `match` batch).
- **Rebuild catch-up.** `build_index` carries the highest exported `seq`; its sink queues `match` jobs for every live key above it, so batches fingerprinted while the index was being built are indexed and matched too. An empty rebuild does the same for anything that arrived meanwhile.
- **Model fence.** `model_switch` cancels queued `fingerprint`/`match` jobs of the previous model; the sinks discard vector results whose model is not the active one (hash and thumbnail are kept); `regroup` ignores vector pairs of other models (the rows and their judgments stay).
- **Restore.** Restoring a trashed image queues a `fingerprint` job for it, since trashing dropped its vectors and thumbnail.
- **Evaluation positives.** Only v1 "related" labels count toward the 20-pair minimum and the suggested thresholds; byte-identical pairs are reported as their own distance set (`positives_identical`). This departs from the task-level plan, which pooled them.
- **Odd files.** Any unexpected exception while embedding one image marks that file `unreadable` with the reason; only a missing model fails the batch.
