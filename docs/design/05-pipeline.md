# 05 · Core Pipeline

```
scan ─► fingerprint ─► match ─► regroup ─► review ─► apply_action ─► archive_move
```

## 7.1 Scan

Walk the Folder's `path` recursively, skipping IgnoredDirectories and taking only extensions that are `enabled` in the Extension table. For each file on disk:

1. Look up File by `(directory, name)`. If it exists with the same `size` and `mtime`: leave it.
2. If it exists but `size` or `mtime` changed: reset `state = new`, clear `sha1`, delete its Fingerprints and its `source = vector` Pairs, and wait for recomputation.
3. If it does not exist: insert with `state = new`.

After the walk, Files present in the database but absent on disk are marked `missing`. Then **move detection** runs:

- Among the files inserted in this scan, those whose `size` equals some `missing` file's size get their SHA-1 computed in place by the server (only these).
- If a `missing` file has exactly one `new` file with the same SHA-1, treat it as a move / rename: update the old record's path to the new location and delete the new record. Fingerprints, Pairs, and judgments are all preserved, and the computed SHA-1 is stored on the File.
- Remaining `missing` files are deleted (with cascades) when the scan finishes. The scan result in the UI lists how many were deleted.

This fixes the v1 problem where moving a file on disk lost all its hashes and judgments.

Scans are one job per Folder, triggered manually or on a schedule (default once a day, can be turned off).

## 7.2 Fingerprint

For each file, a compute child:

1. Computes SHA-1 in streaming fashion.
2. Decodes according to `media_type`:
   - `image`: read with pyvips, apply EXIF orientation, scale to the model input size, one frame.
   - `animated`: decode frames, sample per §7.3.
   - `video`: extract frames with ffmpeg, sample per §7.3.
   - `other`: stop here.
3. Runs every frame through the model to obtain one normalised vector per frame.
4. Generates a 256 px webp thumbnail from the first frame (middle frame for video).
5. Marks decode failures `unreadable`; files whose extension says media but whose content is not (a renamed zip, for example) `not_media`.

On receipt the server writes Fingerprints, stores the thumbnail at `thumbs/{id[:2]}/{id}.webp`, updates the File, and enqueues the batch for `match`.

## 7.3 Frame sampling

- Animated images: take every frame if there are at most 8; otherwise 8 evenly spaced frames.
- Video: 1 frame per second if the duration is at most 8 seconds; otherwise 8 evenly spaced frames plus the first keyframe. Each frame records `frame_time_ms`.
- The cap of 8 frames balances memory against retrieval cost and is an adjustable setting.

Stills and video can be compared because they share one vector space: the vector of a screenshot lands near the vector of the corresponding frame in the video.

## 7.4 Match

Each index entry is one frame vector with payload `(file_id, frame_index)`. The index file is `/data/profiles/<name>/index.usearch`, opened, modified, and saved by the child executing the match.

For a batch of new vectors:

1. Add them to the index.
2. Query the top-k neighbours of each vector (default k = 64) within distance `candidate_distance` (default 0.5, deliberately wide to keep the middle band for human review).
3. Aggregate frame-level hits into file pairs; for one pair of files keep the minimum distance.
4. Apply compare scope: for pair (a, b), discard it if a and b are in the same Folder and that Folder has `compare_scope = other_folders`; discard it if they are in the same Folder and the same `directory` and the Folder has `compare_scope = other_directories`. Files in different Folders are never restricted by this rule.
5. Return the results to the server, which writes Pairs (an existing pair only gets its `distance` updated; `judgment` is untouched).

On k: when a directory holds many variant images, the top-k can fill up with same-directory files and push out a true copy in another directory. Two counter-measures: a larger k, and a payload filter predicate in the usearch query that excludes same-directory entries up front (usearch supports filtered search). Filtering is the primary mechanism, k the fallback.

Pairs of files with equal SHA-1 bypass vectors: when the server receives sha1 values it buckets by hash and writes `source = sha1, distance = 0` Pairs within each bucket (a star from the earliest file is enough; a complete graph is unnecessary because grouping treats them identically). SHA-1 pairs ignore compare scope: byte-identical files are always reported.

If the index is missing or its `model_id` does not match at server start, a `rebuild_index` job is enqueued automatically.

## 7.5 Grouping (regroup)

A Group is a connected component of the Pair graph. An edge exists when `distance ≤ group_distance` (a setting, default: the upper bound of the near-duplicate band) and `judgment != unrelated`. `related_keep` still counts as an edge, preserving v1's Connected semantics.

Incremental: when `match` writes new Pairs or the user changes a judgment, the old Groups of the affected files are marked dirty; `regroup` reruns union-find only over dirty components.

Derived Group attributes:
- `has_identical`: the group contains a `source = sha1` edge.
- `min_distance`: the smallest edge distance in the group.
- `status`:
  - `auto_resolved`: the rules in [06 §8.2](06-review-ui.md#82-default-resolution-rules) need no human (executed or preselected).
  - `open`: needs a human.
  - `resolved`: handled by a human.
  - `dismissed`: the user marked the whole group as not to be acted on (equivalent to marking every edge `related_keep`).

Pairs beyond the grouping threshold but within `candidate_distance` do not join groups, but are visible on the file detail page and feed the review queue.

## 7.6 Model and thresholds

**Model.** First choice: SSCD (Meta's self-supervised copy-detection model, ResNet-50 backbone, 512-dim output). Its training objective is exactly to separate "edited versions of the same picture" from "different pictures", which is this project's pain point. Alternative: DINOv2 ViT-S/14 (384-dim), lighter, but semantic features may misfire differently on variant-image scenarios.

Run an **evaluation** early (milestone 3) before locking in. The eval job opens the v1 database read-only for labels and never writes judgments into the profile:

- Positives: v1 HiddenButConnected pairs plus SHA-1-identical pairs (necessarily visually similar).
- Negatives: v1 HiddenAndDisconnected pairs (noisy; some were bulk mislabels), plus random pairs of files from different directories and different Folders as clean negatives.
- For each candidate model compute the distance distribution, report separation between positives and negatives, false-positive rate at fixed recall, and throughput.
- Write the conclusion back into the acceptance numbers in [00 §1.3](00-overview.md#13-acceptance-criteria) and the `model_id` into Setting.

The evaluation report also answers one decision: is the new model's false-positive rate in the near_duplicate band low enough for "scan with the new version, review by hand"? If yes, stay on the main line; if not, start the optional work in [11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments).

**Threshold bands** (settings; defaults fixed after evaluation):

| Band | Meaning | Default resolution |
|---|---|---|
| identical | `source = sha1` | Fully automatic |
| near_duplicate | Almost certainly a variant of the same picture | Preselected, confirm with one key |
| variant | Differential edits, consecutive frames, local changes | Human review |
| candidate | Weakly related, only for review and detail view | Not grouped |

**Review queue** (optional, see [11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)). If v1 judgment import is enabled, imported `unrelated` pairs whose new-model distance falls in the near_duplicate band are listed for review, to recover bulk mislabels. Not part of the main line.

## 7.7 Changing the model

`Fingerprint.model_id` and `Pair.model_id` carry the version. On a model change, vectors for the new model are computed as a new batch of fingerprint jobs by priority; `match` compares only within one `model_id`; user judgments are kept per file pair, independent of the model. Two vector sets may coexist until the old one is cleaned up. A full recomputation for a model change is another good use of the procedure in [02 §3.1](02-architecture.md#31-initialisation-and-migration) on a powerful machine.
