# Milestone 3 · Similarity engine — Task-level plan

> **Resolution:** task-level. This plan fixes the decomposition, interfaces, tests and acceptance for milestone 3 but does not carry code bodies. Expand each task to executable resolution (the milestone 1 and 2 plan style: failing test → implementation → commit) when milestone 2 has shipped and the decisions listed under "Decide before expanding" are taken. Reason: the model, the thresholds and part of the index layout are outputs of this milestone's own evaluation step, and the fingerprint/result-sink shapes depend on how milestone 2 landed.

**Goal:** Visually similar media is found: every still image gets a copy-detection vector in a per-profile usearch index, new vectors are matched against it, candidate pairs join the sha1 pairs and the grouping, and an evaluation against the imported v1 judgments locks the model and the threshold bands.

**Architecture:** The `fingerprint` compute job grows from SHA-1 to SHA-1 + decode (pyvips) + ONNX inference + 256 px webp thumbnail. Vectors live in `vectors.db` (migration 0002 of the vectors database) and are mirrored into `index.usearch`, a derived file rebuilt from `vectors.db` whenever missing or stale. A `match` compute job adds a batch of vectors to the index and returns raw neighbour hits; its **result sink** applies the compare-scope rule and writes `pair` rows (`source = vector`, `distance`), then marks files dirty for regroup. Threshold bands are profile settings; `group_distance` feeds the existing regroup. An `eval` compute job reads a v1 database read-only and produces a report that decides the model.

**Spec:** [02 §4 (ONNX Runtime, pyvips, usearch)](../design/02-architecture.md#4-technology-stack), [03 §5.1 Fingerprint/Pair, §5.2 vectors.db](../design/03-data-model.md), [04 §6.2 fingerprint/match/rebuild_index/eval, §6.4 one match per profile](../design/04-jobs.md), [05 §7.2, §7.4, §7.6, §7.7](../design/05-pipeline.md), [07 §9.5 resources](../design/07-operations.md#95-resources), [11 §13 item 3, §14 risks, §15](../design/11-roadmap.md). Decisions for this milestone go into `docs/decisions/0005-m3-implementation-choices.md`.

## Inputs from earlier milestones (must be true before expanding)

- Milestone 2 shipped: `fingerprint` job, `RESULT_SINKS`, `regroup_queue`, `pair` table with `source`/`distance`/`model_id`, `grouping.group_distance` setting, group detail page.
- The image (`docker/Dockerfile`) has `libvips42` with `heifload yes` on both architectures (verified 2026-11-06 in the first publish run) and `ffmpeg`.

## Decide before expanding (recorded in ADR-0005)

1. **Model artefact distribution.** Recommendation: download on first use into `/data/models/<model_id>.onnx` with a pinned sha256, optional `VISUALSTORE_MODEL_DIR` for offline installs; the image does not embed the weights (keeps it small and licence-clean).
2. **usearch key encoding.** Recommendation: 64-bit key = `(dir_key: 32 bits) << 32 | seq: 32 bits` where `dir_key` hashes `(folder_id, directory)` and `seq` is a per-profile counter stored in `vectors.db` (`index_key` table: `seq`, `file_id`, `frame_index`, `dir_key`). The child can then apply the "exclude same directory" filter predicate from the key alone, with no database access.
3. **Where compare scope is applied.** Recommendation: in the match result sink (server side), as the design says; store only pairs that pass. Alternative recorded for the user's earlier question: store all candidate pairs and filter at grouping (costs Pair-table size on CG libraries, gains free scope changes).
4. **Threshold defaults** are provisional until the eval report; ship `candidate_distance = 0.5`, `near_duplicate = 0.15`, `variant = 0.3`, `group_distance = 0.3` and write the measured values back into [00 §1.3](../design/00-overview.md#13-acceptance-criteria).
5. **Second model for evaluation**: DINOv2 ViT-S/14 (384-dim) alongside SSCD ResNet-50 (512-dim), per §7.6.

## Global Constraints (in addition to milestones 1–2)

- Children load the ONNX session lazily, once per process, and keep it for the process lifetime. The pool size therefore bounds memory: document the per-child footprint in the README.
- `index.usearch` has one writer: the scheduler allows at most one `match` or `rebuild_index` job in flight per profile (compute slot accounting gains a per-profile exclusivity flag).
- Every vector is L2-normalised float16; `distance` is cosine distance in `[0, 2]` stored as REAL; sha1 pairs stay at 0.
- `model_id` is a string such as `sscd-resnet50-512-v1`; vectors, index metadata and `pair.model_id` all carry it. A profile has one active model (`model.id` setting); changing it does not delete old vectors (design §7.7).
- Thumbnails: `thumbs/{id[:2]}/{id}.webp`, 256 px longest side, generated from the first frame; never backed up.
- Decode failures mark `unreadable`; wrong-content files mark `not_media`; both are reported, never raised.

## Review Focus (to pin with tests when expanding)

1. A profile whose `index.usearch` is missing, truncated, or built for another `model_id` must trigger `rebuild_index` at start-up and must not serve matches from the stale index meanwhile.
2. Two files in the same directory of a Folder with `compare_scope = other_directories` must never form a vector pair, while the same two files in different Folders always may; sha1 pairs ignore scope entirely.
3. A model change must leave existing judgments and groups intact, compute vectors only for files lacking them for the new model, and never mix distances of two models in one grouping pass.
4. An image that decodes but is not what its extension claims (a renamed zip) and an image larger than the configured pixel limit must end as `not_media` / `unreadable` without killing the compute child.
5. The eval job must refuse to write anything to the profile (no pairs, no judgments) and must tolerate v1 SimilarFile rows that reference missing files.

## File structure

```
visualstore/
  db/migrations/vectors/0002_fingerprint.sql   fingerprint(file_id, frame_index, frame_time_ms, vector BLOB, model_id), index_key(seq PK, file_id, frame_index, dir_key), index_meta(model_id, dim, built_at)
  db/fingerprints.py                           insert_frames, frames_of_file, files_missing_model, delete_for_file, next_seq
  media/decode.py                              load_image(path, max_pixels) -> Frame; to_model_input(frame, size); thumbnail(frame, 256) -> webp bytes
  media/errors.py                              NotMedia, Unreadable
  models/registry.py                           MODELS: id -> ModelSpec(url, sha256, dim, input_size, mean, std); resolve_path(model_id, model_dir)
  models/infer.py                              Session (lazy onnxruntime), embed(batch) -> np.ndarray float32 normalised
  index/usearch_index.py                       open_or_create(path, dim), add(keys, vectors), search(vectors, k, filter), save, key encode/decode, dir_key()
  domain/scope.py                              allowed(pair_scope_info) -> bool for the three compare_scope levels
  domain/bands.py                              band(distance, settings) -> identical | near_duplicate | variant | candidate
  jobs/compute/handlers.py                     fingerprint (+decode/infer/thumb), match, rebuild_index, eval
  jobs/results.py                              sink_fingerprint (+frames, thumbnails, enqueue match), sink_match (scope filter, pair upsert, dirty), sink_rebuild_index, sink_eval
  jobs/scheduler.py                            per-profile exclusive compute types {match, rebuild_index}
  jobs/io/model_switch.py                      io job: set model.id, enqueue fingerprints for files missing vectors, rebuild_index
  api/thumbnails.py                            GET /files/{id}/thumbnail
  api/settings.py                              + model & threshold keys surfaced
  api/eval.py                                  POST /eval, GET /eval/reports, GET /eval/reports/{name}
web/src/pages/GroupDetail.tsx                  thumbnails per member (no comparison pane yet)
web/src/pages/Jobs.tsx                         model selector, threshold settings, eval trigger and report list
docker/Dockerfile                              onnxruntime, usearch, numpy, pyvips in the `media` extra; model download at first use
```

## Tasks

1. **Vectors schema and fingerprint queries** — `0002_fingerprint.sql`, `db/fingerprints.py`. Tests: insert/replace frames per file and model; `files_missing_model` returns only files whose current state is fingerprinted and lack vectors for the model; deleting a file removes its frames (no FK across databases → explicit delete in `files.delete_files` via a hook).
2. **Model registry and download** — `models/registry.py`, sha256 verification, `VISUALSTORE_MODEL_DIR`, ADR-0005. Tests: resolve from an offline dir; corrupt download rejected and deleted; unknown model id is a `ConfigError`-class failure at profile setting time, not at inference time.
3. **Image decoding and thumbnails** — `media/decode.py` with pyvips: EXIF orientation, max-pixel guard, centre-crop/resize to model input, webp thumbnail. Tests use generated images (solid, gradient, noise; rotated via EXIF; a renamed zip → `NotMedia`; a 1×1 and a huge-dimension header → `Unreadable`). (Review Focus 4.)
4. **ONNX inference** — `models/infer.py` lazy session, batch embedding, normalisation; tests with a tiny generated ONNX model (identity-like) to avoid downloading weights in CI; one marked-slow test with the real SSCD model behind an env flag.
5. **Fingerprint handler extension and sink** — media types `image` → sha1 + vector + thumbnail; `animated`/`video` → first frame only in this milestone (milestone 5 adds sampling); `other` → sha1. Result gains `frames` (base64 f16) and `thumbnail` (base64 webp). Sink writes frames and thumbnail, sets state, and enqueues `match` for the batch at the job's priority. Tests: result shape, idempotent re-run, thumbnail path layout, sink enqueues exactly one match per fingerprint batch.
6. **usearch wrapper and key encoding** — `index/usearch_index.py`; tests: add/search round trip, filter predicate excludes same `dir_key`, save/load, metadata check for `model_id` and dim, open on a corrupt file raises a typed error.
7. **Match handler and sink** — child: open index, add batch, top-k within `candidate_distance` with same-directory filter, return raw hits `(file_id, frame_index, neighbour_file_id, neighbour_frame_index, distance)`; sink: aggregate to file pairs (min distance), apply `domain/scope.py`, upsert `pair(source=vector, distance, model_id)` keeping judgments, mark dirty, enqueue regroup. Tests: scope matrix (Review Focus 2), min-distance aggregation, judgment preserved on re-match, sha1 pairs untouched.
8. **Scheduler exclusivity and index rebuild** — one `match`/`rebuild_index` in flight per profile; `rebuild_index` handler streams all frames from `vectors.db` (passed as a file path to the child? no — children have no DB: the server exports a temporary `.npy` + keys file and passes its path) and writes a fresh index; start-up check enqueues rebuild when the index is missing or `index_meta.model_id` differs (Review Focus 1). Tests with FakePool for exclusivity; integration for rebuild.
9. **Bands and threshold settings** — `domain/bands.py`, settings `similarity.candidate_distance`, `similarity.near_duplicate`, `similarity.variant`, existing `grouping.group_distance`; `regroup` reads `group_distance`; group list API gains `band` (from `min_distance`) and a `band` filter. Tests: band boundaries inclusive/exclusive as documented; regroup splits when `group_distance` shrinks.
10. **Eval job and report** — `eval` compute handler takes the v1 db path (read-only) and a vectors export; labels per §7.6 (positives: HiddenButConnected + sha1-equal; negatives: HiddenAndDisconnected + random cross-Folder pairs); outputs distance distributions, FP rate at fixed recall, separation, throughput; sink writes `reports/eval-<model>-<timestamp>.{json,md}` under the profile directory. API lists and serves reports. Tests: synthetic vectors with known separation produce the expected numbers; v1 orphans tolerated; nothing written to pair/judgment tables (Review Focus 5).
11. **Model switch** — `model_switch` io job: validates the model id, sets `model.id`, enqueues `fingerprint` for `files_missing_model` in batches by Folder priority, enqueues `rebuild_index`; `match` compares only within the active `model_id`. Tests: judgments and groups untouched; only missing files queued; mixed-model distances never grouped together (Review Focus 3).
12. **API and frontend** — thumbnail endpoint (`FileResponse` of the webp, 404 when absent); group list/detail show thumbnails; Jobs page: model selector (from the registry), threshold settings, "Run evaluation" with v1 db path, report list with the markdown rendered as preformatted text.
13. **Image and resources** — Dockerfile installs the `media` extra (`onnxruntime`, `usearch`, `numpy`, `pyvips`); README documents model download, offline dir, per-child memory, `VISUALSTORE_COMPUTE_PROCESSES` guidance for a NAS; compose comment for a models volume.
14. **Evaluation run and lock-in** — not code: run the eval on the user's imported v1 data for SSCD and DINOv2, write the report path and the chosen thresholds into [00 §1.3](../design/00-overview.md#13-acceptance-criteria) and ADR-0005, decide whether [11 §15](../design/11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments) is needed.

## Acceptance

- A profile with a few thousand still images scans, fingerprints and matches end to end on the development machine; near-duplicate groups appear in the queue with thumbnails.
- The eval report exists for both candidate models and the chosen model's numbers are recorded in the design (recall on confirmed-related ≥ pHash's; false-positive rate on confirmed-unrelated well below pHash's).
- Deleting `index.usearch` and restarting rebuilds it with no manual step.
- All milestone 1–2 tests still pass; new unit tests run without model downloads (CI-safe).
