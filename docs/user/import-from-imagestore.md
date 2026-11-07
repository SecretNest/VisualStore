# Import from ImageStore (v1)

ImageStore's SQLite database imports in one step: Folder configuration, files, SHA-1 hashes and
byte-identical groups are kept with their ids. pHash values are dropped; similarity is recomputed
with the new model. ImageStore's manual "related" / "unrelated" judgments are used to evaluate
the model and are not imported by default.

## Prerequisites

- An **empty profile** (no Folders yet).
- The v1 `.db` file mounted into the container (for example `-v /volume1/ImageStore:/import:ro`).
- The media mounted at the container paths you will keep.

## Run the import

**Jobs → Import an ImageStore (v1) database**: enter the container path of the `.db`, then one
row per v1 Folder: its v1 name and the container path where its files are now. Click Import and
watch the job. The result lists imported Folders, files and groups and any skipped rows.

Then **Folders → Scan now** on each Folder: the scan confirms every imported file on disk
(detecting moves and changes) and queues fingerprinting for the vectors the import could not
bring along.

## Evaluate the model against your judgments

Once fingerprinting has finished, **Jobs → Evaluation against an ImageStore (v1) database**:
give the same `.db` path and (optionally) the number of random negative pairs. The job writes
`eval-<model>-<timestamp>.md` and `.json` to `/data/profiles/<profile>/reports/` and links them
on the same page. The report shows:

- distance distributions of v1 "related" pairs, v1 "unrelated" pairs and random pairs;
- the false-positive rate at 90 %, 95 % and 99 % recall of the related pairs;
- d′ and AUC;
- how the current threshold bands classify each set, and suggested `similarity.near_duplicate`
  (95 % recall) and `similarity.variant` (99 % recall) values.

Set the thresholds under **Jobs → Settings** (`similarity.*`, `grouping.group_distance`). The
bands follow the `similarity.*` values at once; changing `grouping.group_distance` queues a regroup
of every paired file, so the groups are re-formed at the new distance. Running the evaluation for the comparison model (DINOv2) means
switching the model (**Jobs → Model**), waiting for the vectors, and running it again.
