# Initialise on a strong machine, then migrate to the NAS

The first full computation of a large library (inference over every file) is heavy. Do it once
on a powerful machine, then move the data directory to the NAS, which only has to keep up with
new arrivals.

## 1. Start on the strong machine

1. Mount the media so that the **container paths** are the ones the NAS will use later, for
   example `/media/images`. Over SMB: mount with cifs on the host and bind-mount into the
   container. Keep `/data` on a local disk.
2. Use the same `compose.yaml` you will use on the NAS, changing only the host side of the mounts.
3. `docker compose up -d`, open `http://<host>:8080`, log in.

## 2. Create the profile and Folders

1. **Profiles → Create**: one profile per independent library.
2. **Folders → Create Folder**: name, container path (`/media/images`), role (`archive` for the
   curated collection, `inbox` for new arrivals), compare scope (`all`, or
   `other_directories` for CG libraries where files in one directory are intentional variants).
   Add IgnoredDirectories if needed.
3. Importing from ImageStore? Do it now, before the first scan: see
   [Import from ImageStore](import-from-imagestore.md).
4. **Scan now** on each Folder.

## 3. Wait for the pipeline

The **Jobs** page shows `scan` → `fingerprint` (hashing, decoding, inference, thumbnails, batches
of 64 files) → `match` (neighbour search) → `regroup`. The **Overview** page shows per-Folder
counts by state. It is finished when no job is queued or running and the Groups page shows
`0 files waiting for regrouping`.

Throughput is dominated by inference: a few hundred milliseconds per image per compute child.
Raise `VISUALSTORE_COMPUTE_PROCESSES` on the strong machine; lower it on the NAS.

## 4. Stop and copy

```bash
docker compose down
rsync -a ./data/ nas:/volume1/docker/visualstore/data/      # the whole directory, as-is
```

Everything travels: databases, the search index, thumbnails, previews. Nothing is recomputed on
the NAS. Do **not** copy while the container runs.

## 5. Start on the NAS

Same `compose.yaml`, host mounts adjusted, `docker compose up -d`. The start-up checks compare
the index with the databases and find them consistent; the first scheduled scan picks up new
arrivals and computes only those.

What must stay identical between the two machines: the container-side media paths and the
profile names. What may differ: host paths, CPU count, `PUID`/`PGID` (set them to the owner of
the copied directory, or chown it).
