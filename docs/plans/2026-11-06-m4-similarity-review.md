# Milestone 4 · Similarity review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> This plan was expanded from the task-level plan of the same name after milestone 3 shipped. The "Decide before expanding" items are decided as recorded in [ADR-0006](../decisions/0006-m4-implementation-choices.md) (written in Task 1): all three comparison modes; one `bulk_resolve` io job that trashes inline; a cached 1024 px preview endpoint.

**Goal:** Resolving a similarity group becomes a three-keystroke job: the group detail page shows the selected member next to its nearest neighbour with side-by-side, overlay and blink comparison; the file detail page lists candidate pairs beyond the grouping threshold with judgment buttons and move/rename; the review queue applies the default rules to every group matching a filter in one job after a confirmation that states what will be deleted.

**Architecture:** Frontend-heavy. The backend adds the nearest neighbour per member to the group detail response (pure helper over the group's inner pairs), a `preview` endpoint that renders a 1024 px webp on demand into `previews/` (pyvips in a thread, same hardened loading as fingerprinting), `candidates` on the file detail response, a `bulk` API (synchronous preview over the queue filter, execution as one `bulk_resolve` io job that re-validates every group and trashes inline through the existing `apply_action` core), and `move`/`rename` items in `apply_action`. The group resolution logic moves out of the API handler into `jobs/resolve.py` so the bulk job and the endpoint share it. No schema change.

**Tech Stack:** Python 3.12, FastAPI, pyvips (preview rendering), React + TypeScript, vitest, Playwright. No new dependencies.

**Spec:** [06 §8.1 pages 3–4, §8.3, §8.4, §8.5 move/rename](../design/06-review-ui.md), [05 §7.5 last paragraph](../design/05-pipeline.md#75-grouping-regroup), [01 §2.2 bulk rules](../design/01-requirements.md#22-new-requirements), [11 §13 item 4](../design/11-roadmap.md#13-milestones).

## Global Constraints

- Everything in `CLAUDE.md`: single English docs tree, no `docs/superpowers/`, Conventional Commits with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer, UI copy in English.
- Every FastAPI handler and dependency is `async def` (structural test `tests/integration/test_api_async.py`); blocking work (pyvips) runs through `asyncio.to_thread`.
- The server process must import without libvips (`pyvips` is imported lazily inside the preview handler); tests needing libvips use `tests/fixtures/vips.require_pyvips()` and run in the `visualstore:test` image.
- Bulk execution never deletes a file the default rules would leave `undecided`; groups that are not fully preselected are skipped and counted.
- Every bulk run shows the exact count of files to delete and the Folders affected before the user confirms; when the count exceeds `bulk.confirm_threshold` (default 100) the user must type the count back.
- Keyboard shortcuts from milestone 2 are kept (`1–9`, `↑/↓`, `k`, `d`, `a`, `u`, `Enter`, `Esc`); new keys: `s` side-by-side, `o` overlay, `b` blink, `n`/`p` next/previous comparison target, `Space` swaps the two compared members.
- Every action performs the disk operation first and changes the database only on success (design §8.5).
- `ruff check` / `ruff format --check` clean; `cd web && npm run lint && npm test && npm run build` clean; e2e green (`npm run e2e`, which uses the `visualstore:test` image when libvips is absent locally).

## Review Focus

1. **A bulk rule whose filter matches no fully-preselected group** must do nothing and say so: preview reports zero planned groups, execute answers 400 and enqueues nothing. (Task 6.)
2. **A group that changes between preview and execution** (regroup, judgment, trash) must not get stale decisions: execute refuses a stale token (409), and the job re-validates each group's member set and recomputed rules, skipping the ones that changed. (Task 6.)
3. **`move`/`rename` to a path that exists** (on disk or in the table) must fail that item without touching the disk; a cross-Folder move must update `folder_id`, keep id / sha1 / pairs, and mark the file dirty. (Task 8.)
4. **A missing or undecodable original** (deleted outside VisualStore, corrupt file) must leave the comparison pane usable: the preview endpoint answers 404 with a reason and the pane falls back to the thumbnail with a notice, never a broken image. (Tasks 2 and 3.)
5. **Candidate pairs on the file detail page** are exactly the vector pairs with `group_distance < distance ≤ candidate_distance` that are not judged `unrelated`. (Task 5.)

---

## File structure

```
visualstore/
  domain/neighbours.py          nearest(members, pairs) — pure
  domain/bulk.py                GroupPlan, BulkPlan, signature(), plan_group(), build_plan() — pure
  jobs/resolve.py               commit_decisions(conn, group_id, decisions) shared by API and bulk job
  jobs/bulkplan.py              plan_for_group(conn, settings, row), plan_for_filter(conn, settings, filter)
  jobs/io/bulk_resolve.py       run_bulk_resolve(ctx)
  jobs/io/actions.py            execute_action() extracted from run_apply_action; move/rename items; move_file(allow_suffix)
  media/decode.py               + preview_webp(path, longest, max_pixels)
  profiles.py                   + ProfilePaths.previews
  db/files.py                   + update_location(conn, file_id, folder_id, directory, name)
  db/pairs.py                   + inner_pairs(conn, member_ids)
  db/settings.py                + bulk.confirm_threshold
  api/groups.py                 detail gains neighbours; resolve uses jobs/resolve
  api/bulk.py                   POST /groups/bulk/preview, POST /groups/bulk/execute
  api/files.py                  + /preview, candidates + media_type in detail, POST /move, POST /rename
  jobs/results.py               sink_fingerprint drops a stale preview
web/src/pages/groupKeys.ts      mode / target keys
web/src/components/compareLayout.ts   layers(mode, tick, opacity) — pure
web/src/components/Compare.tsx  side-by-side / overlay / blink with thumbnail fallback
web/src/pages/GroupDetail.tsx   comparison pane, neighbour distances, details links
web/src/pages/FileDetail.tsx    file info, candidates with judgments, move / rename
web/src/pages/Groups.tsx        bulk rule panel
web/src/types.ts                GroupDetail.neighbours, FileDetail, Candidate, BulkPreview
docs/decisions/0006-m4-implementation-choices.md
```

---

### Task 1: ADR-0006 and the nearest neighbour per member

**Files:**
- Create: `docs/decisions/0006-m4-implementation-choices.md`, `visualstore/domain/neighbours.py`, `tests/unit/test_domain_neighbours.py`
- Modify: `docs/decisions/README.md`, `visualstore/api/groups.py`, `tests/integration/test_api_groups.py`, `web/src/types.ts`

**Interfaces:**
- `nearest(members: dict[bytes, str], pairs: Iterable[tuple[bytes, bytes, float | None, str]]) -> dict[bytes, tuple[bytes, float] | None]` — members map file id → relative path; pairs are `(a, b, distance, judgment)`; sha1 pairs (`distance None`) count as 0.0; `unrelated` pairs are ignored; ties go to the neighbour that sorts first by `path_key`.
- `GET /api/p/{name}/groups/{id}` gains `"neighbours": {member_id: {"file_id": str, "distance": float} | null}`.

- [ ] **Step 1: Write the ADR**

`docs/decisions/0006-m4-implementation-choices.md`:

```markdown
# ADR-0006: Milestone 4 implementation choices

Date: 2026-11-06 · Status: accepted

## Context

The milestone 4 task-level plan listed decisions to take before expansion.

## Decisions

- **Comparison modes**: side-by-side, overlay with an opacity slider, and blink (two images alternating every 500 ms). All three are small once both images are loaded; the key model (`groupKeys.ts`) carries the mode so it is testable without React.
- **Nearest neighbour travels with the group detail** instead of a separate endpoint: it is derived from the inner pairs the detail already loads, and one request keeps the page simple.
- **Preview images** are 1024 px webp files rendered on demand by the server with pyvips (`thumbnail_source`, shrink-on-load, same `Source`-based loading and `fail_on="truncated"` as fingerprinting) and cached under `previews/{id[:2]}/{id}.webp` next to `thumbs/`. The fingerprint sink deletes the cached preview when it writes a new thumbnail, so a changed file never shows a stale preview. Maintenance (milestone 6) may clear the directory at any time. The original stays available through `/content`.
- **Bulk rules** run as one `bulk_resolve` io job. The preview is a synchronous API call over the queue filter that applies the default rules to every open group and returns the planned groups, the skipped count, the number of files to delete, the Folders affected and a token (hash of the planned group ids and their member sets). Execution requires the same token (409 when the queue changed), the typed count when it exceeds `bulk.confirm_threshold`, and then the job re-validates each group (member set and recomputed rules) and trashes inline through the `apply_action` core, so one job row shows the progress and nothing else is enqueued.
- **Move and rename** are `apply_action` items (`move`, `rename`) rather than new job types; they never overwrite: an existing target on disk or in the table fails that item before the disk is touched.
- **Group resolution logic** (store decisions, mark kept pairs `related_keep`, trash or resolve) lives in `visualstore/jobs/resolve.py`, shared by the endpoint and the bulk job.

## Consequences

- `previews/` is derived data like `thumbs/`; backups do not include it.
- A bulk run over thousands of groups is one job; cancelling it (queued only) is cheap, interrupting it mid-way leaves already-processed groups resolved, which is correct since each group is committed independently.
```

Add `| [0006](0006-m4-implementation-choices.md) | Milestone 4 implementation choices: comparison modes, neighbours in detail, cached previews, bulk job, move/rename as actions | accepted |` to the table in `docs/decisions/README.md`.

- [ ] **Step 2: Write the failing unit test**

`tests/unit/test_domain_neighbours.py`:

```python
from visualstore.domain.neighbours import nearest

A, B, C, D = b"a" * 16, b"b" * 16, b"c" * 16, b"d" * 16
MEMBERS = {A: "x/a.jpg", B: "x/b.jpg", C: "x/c.jpg", D: "x/d.jpg"}


def test_nearest_picks_the_smallest_distance_and_treats_sha1_as_zero():
    pairs = [(A, B, 0.1, "none"), (B, C, 0.05, "none"), (A, C, None, "none")]
    got = nearest(MEMBERS, pairs)
    assert got[A] == (C, 0.0)
    assert got[B] == (C, 0.05)
    assert got[C] == (A, 0.0)
    assert got[D] is None  # no usable pair touches it


def test_ties_go_to_the_neighbour_that_sorts_first_by_path_and_unrelated_is_ignored():
    pairs = [(A, C, 0.1, "none"), (A, B, 0.1, "none"), (C, D, 0.01, "unrelated")]
    got = nearest(MEMBERS, pairs)
    assert got[A] == (B, 0.1)
    assert got[D] is None
    assert got[C] == (A, 0.1)


def test_pairs_outside_the_member_set_are_ignored():
    pairs = [(A, b"z" * 16, 0.0, "none"), (A, A, 0.0, "none")]
    assert nearest({A: "a.jpg"}, pairs) == {A: None}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_domain_neighbours.py -q` — Expected: FAIL (`visualstore.domain.neighbours` missing).

- [ ] **Step 4: Implement**

`visualstore/domain/neighbours.py`:

```python
"""Nearest group member for every member (design §8.3: distance to the nearest member). Pure."""

from __future__ import annotations

from collections.abc import Iterable

from visualstore.domain.paths import path_key


def nearest(
    members: dict[bytes, str], pairs: Iterable[tuple[bytes, bytes, float | None, str]]
) -> dict[bytes, tuple[bytes, float] | None]:
    """`members` maps file id -> relative path; `pairs` are (a, b, distance, judgment).

    Per member: the other member at the smallest distance (sha1 pairs count as 0.0; ties go to
    the neighbour whose path sorts first), or None when no usable pair touches it."""
    best: dict[bytes, tuple[float, str, bytes]] = {}
    for a, b, distance, judgment in pairs:
        if judgment == "unrelated" or a == b or a not in members or b not in members:
            continue
        d = 0.0 if distance is None else float(distance)
        for me, other in ((a, b), (b, a)):
            candidate = (d, path_key(members[other]), other)
            if me not in best or candidate < best[me]:
                best[me] = candidate
    return {m: ((best[m][2], best[m][0]) if m in best else None) for m in members}
```

- [ ] **Step 5: Run the unit test, then write the failing API test**

Run: `uv run pytest tests/unit/test_domain_neighbours.py -q` — Expected: 3 passed.

Append to `tests/integration/test_api_groups.py`:

```python
def test_detail_carries_the_nearest_neighbour_per_member(client, seeded):
    s = seeded
    detail = client.get(f"/api/p/a/groups/{id_to_str(s['gid'])}").json()
    near = detail["neighbours"]
    a, b, c = (id_to_str(s[k]) for k in "abc")
    assert near[a] == {"file_id": b, "distance": 0.0}  # tie with c: b.jpg sorts first
    assert near[b] == {"file_id": a, "distance": 0.0}
    assert near[c] == {"file_id": a, "distance": 0.0}
```

Run: `uv run pytest tests/integration/test_api_groups.py -q -k neighbour` — Expected: FAIL (KeyError `neighbours`).

- [ ] **Step 6: Implement the API change**

In `visualstore/api/groups.py` add `from visualstore.domain.neighbours import nearest` and replace `get_group`:

```python
@router.get("/groups/{group_id}")
async def get_group(group_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    gid = parse_id(group_id)
    row = _group_or_404(conn, gid)
    members = groups_db.members(conn, gid)
    paths = {bytes(m["file_id"]): groups_db.member_to_dict(m)["relative_path"] for m in members}
    inner = _inner_pairs(conn, set(paths))
    near = nearest(
        paths,
        [(bytes(p["file_a"]), bytes(p["file_b"]), p["distance"], p["judgment"]) for p in inner],
    )
    return {
        "group": _group_out(conn, row),
        "pairs": [pairs_db.pair_to_dict(p) for p in inner],
        "neighbours": {
            id_to_str(m): ({"file_id": id_to_str(n[0]), "distance": n[1]} if n else None)
            for m, n in near.items()
        },
    }
```

In `web/src/types.ts`, extend `GroupDetail`:

```ts
export interface Neighbour {
  file_id: string;
  distance: number;
}

export interface GroupDetail {
  group: Group;
  pairs: Pair[];
  neighbours: Record<string, Neighbour | null>;
}
```

- [ ] **Step 7: Run the tests, lint, commit**

Run: `uv run pytest tests/integration/test_api_groups.py tests/unit/test_domain_neighbours.py -q && uv run ruff check . && uv run ruff format . && (cd web && npm run lint)` — Expected: all pass.

```bash
git add docs/decisions/0006-m4-implementation-choices.md docs/decisions/README.md visualstore/domain/neighbours.py visualstore/api/groups.py tests/unit/test_domain_neighbours.py tests/integration/test_api_groups.py web/src/types.ts
git commit -m "feat: nearest neighbour per group member; ADR-0006 for milestone 4 choices

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Preview endpoint with a cache

**Files:**
- Modify: `visualstore/media/decode.py`, `visualstore/profiles.py`, `visualstore/api/files.py`, `visualstore/jobs/results.py`, `tests/unit/test_decode.py`
- Create: `tests/integration/test_api_preview.py`

**Interfaces:**
- `preview_webp(path: str, longest: int, max_pixels: int) -> bytes` — webp bytes, longest side ≤ `longest`, never upscaled; raises `NotMedia` / `Unreadable` like `load_image`.
- `ProfilePaths.previews` → `<root>/previews` (created with the profile).
- `GET /api/p/{name}/files/{id}/preview` → `image/webp` (1024 px, cached at `previews/{id[:2]}/{id}.webp`); 404 `"No such file."` / `"The file is not on disk."` / `"No preview for this media type yet."` / `"Cannot decode the file: …"`.
- `sink_fingerprint` deletes `previews/{id[:2]}/{id}.webp` whenever it writes a thumbnail for that file.

- [ ] **Step 1: Write the failing decode test**

Append to `tests/unit/test_decode.py` (the file already starts with `pyvips = require_pyvips()` and imports `png_bytes`, `garbage`, `truncated` from `tests.fixtures.images`; add `preview_webp` to the `visualstore.media.decode` import):

```python
def test_preview_webp_downscales_to_the_longest_side_and_never_upscales(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(png_bytes(1600, 1200, gradient=True))
    out = pyvips.Image.new_from_buffer(preview_webp(str(big), 1024, 100_000_000), "")
    assert (out.width, out.height) == (1024, 768)
    small = tmp_path / "small.png"
    small.write_bytes(png_bytes(40, 30))
    out = pyvips.Image.new_from_buffer(preview_webp(str(small), 1024, 100_000_000), "")
    assert (out.width, out.height) == (40, 30)


def test_preview_webp_raises_like_load_image(tmp_path):
    bad = tmp_path / "zip.jpg"
    bad.write_bytes(garbage())
    with pytest.raises(NotMedia):
        preview_webp(str(bad), 1024, 100_000_000)
    cut = tmp_path / "cut.png"
    cut.write_bytes(truncated(png_bytes(400, 300, gradient=True)))
    with pytest.raises(Unreadable):
        preview_webp(str(cut), 1024, 100_000_000)
    huge = tmp_path / "huge.png"
    huge.write_bytes(png_bytes(400, 300))
    with pytest.raises(Unreadable, match="exceeds"):
        preview_webp(str(huge), 1024, 1000)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker run --rm -v "$PWD/visualstore:/app/visualstore:ro" -v "$PWD/tests:/app/tests:ro" visualstore:test python -m pytest -p no:cacheprovider tests/unit/test_decode.py -q` — Expected: FAIL (`preview_webp` missing).

- [ ] **Step 3: Implement `preview_webp` and the paths**

Append to `visualstore/media/decode.py`:

```python
def preview_webp(path: str, longest: int, max_pixels: int) -> bytes:
    """A down-scaled webp of an image file for the comparison pane (design §8.3). Shrink-on-load
    through a Source, same hardening as load_image; never upscales."""
    try:
        source = pyvips.Source.new_from_file(path)
        probe = pyvips.Image.new_from_source(source, "", access="sequential", fail_on="truncated")
        if probe.width * probe.height > max_pixels:
            raise Unreadable(f"{probe.width}x{probe.height} exceeds the limit of {max_pixels} pixels")
        img = pyvips.Image.thumbnail_source(
            pyvips.Source.new_from_file(path),
            longest,
            height=longest,
            size="down",
            fail_on="truncated",
        )
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        return img.copy_memory().write_to_buffer(".webp[Q=85]")
    except pyvips.Error as exc:
        message = str(exc)
        if any(marker in message for marker in _NOT_MEDIA_MARKERS):
            raise NotMedia(message) from exc
        raise Unreadable(message) from exc
```

In `visualstore/profiles.py` add to `ProfilePaths`:

```python
    @property
    def previews(self) -> Path:
        return self.root / "previews"
```

and where `create()` does `paths.thumbs.mkdir(parents=True, exist_ok=True)`, add `paths.previews.mkdir(parents=True, exist_ok=True)`.

- [ ] **Step 4: Run the decode test**

Run the Docker command from Step 2 — Expected: all decode tests pass.

- [ ] **Step 5: Write the failing API test**

`tests/integration/test_api_preview.py`:

```python
from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.vips import require_pyvips
from tests.integration.conftest import login
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders
from visualstore.ids import id_to_str
from visualstore.jobs.results import sink_fingerprint

pyvips = require_pyvips()


def seed(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)

    def add(name, data, media="png"):
        (lib / name).write_bytes(data)
        st = (lib / name).stat()
        return files.insert_file(
            conn, folder_id=folder, directory="", name=name, extension_id=ext[media]["id"], size=st.st_size, mtime=st.st_mtime_ns
        )

    return reg, lib, add


def test_preview_is_rendered_once_cached_and_served(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    fid = id_to_str(add("big.png", png_bytes(1600, 1200, gradient=True)))
    got = client.get(f"/api/p/a/files/{fid}/preview")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
    img = pyvips.Image.new_from_buffer(got.content, "")
    assert (img.width, img.height) == (1024, 768)
    cache = reg.paths("a").previews / fid[:2] / f"{fid}.webp"
    assert cache.is_file() and not list(cache.parent.glob("*.tmp"))
    cache.write_bytes(b"RIFFcached")  # served from the cache from now on
    assert client.get(f"/api/p/a/files/{fid}/preview").content == b"RIFFcached"
    # a new fingerprint (changed file) drops the cached preview
    sink_fingerprint(
        make_sink_ctx(reg, "a", {}, {"files": [{"id": fid, "sha1": "00" * 20, "state": "fingerprinted", "thumbnail": "AAAA"}]})
    )
    assert not cache.exists()


def test_preview_failures_are_404_with_a_reason(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    gone = id_to_str(add("gone.png", png_bytes(40, 30)))
    (lib / "gone.png").unlink()
    assert client.get(f"/api/p/a/files/{gone}/preview").json()["detail"] == "The file is not on disk."
    bad = id_to_str(add("bad.png", garbage()))
    r = client.get(f"/api/p/a/files/{bad}/preview")
    assert r.status_code == 404 and r.json()["detail"].startswith("Cannot decode the file")
    video = id_to_str(add("clip.mp4", b"not decoded yet", media="mp4"))
    assert client.get(f"/api/p/a/files/{video}/preview").json()["detail"] == "No preview for this media type yet."
    assert client.get("/api/p/a/files/00000000-0000-4000-8000-000000000000/preview").status_code == 404
```

Run (Docker, as above, with `tests/integration/test_api_preview.py`) — Expected: FAIL (404 for the first request: route missing).

- [ ] **Step 6: Implement the endpoint and the sink invalidation**

In `visualstore/api/files.py` add `import asyncio`, `from visualstore.db import settings as settings_db`, `from visualstore.media.errors import NotMedia, Unreadable`, `PREVIEW_PX = 1024`, and the route (before `/{file_id}/thumbnail` is fine; order does not matter for distinct paths):

```python
def _media_type(conn, extension_id: bytes) -> str:
    for r in folders_db.list_extensions(conn):
        if bytes(r["id"]) == extension_id:
            return r["media_type"]
    return "other"


@router.get("/{file_id}/preview")
async def file_preview(
    file_id: str, request: Request, name: str = Depends(get_profile)
) -> FileResponse:
    state = get_state(request)
    conn = state.registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    cache = state.registry.paths(name).previews / file_id[:2] / f"{file_id}.webp"
    if cache.is_file():
        return FileResponse(cache, media_type="image/webp")
    path = Path(folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The file is not on disk.")
    if _media_type(conn, bytes(row["extension_id"])) != "image":
        raise HTTPException(status_code=404, detail="No preview for this media type yet.")
    from visualstore.media.decode import preview_webp  # pyvips: not imported server-wide

    max_pixels = int(settings_db.get(conn, "media.max_pixels"))
    try:
        data = await asyncio.to_thread(preview_webp, str(path), PREVIEW_PX, max_pixels)
    except (NotMedia, Unreadable) as exc:
        raise HTTPException(status_code=404, detail=f"Cannot decode the file: {exc}") from None
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(cache)
    return FileResponse(cache, media_type="image/webp")
```

In `visualstore/jobs/results.py`, `sink_fingerprint`, where the thumbnail is written:

```python
        if entry.get("thumbnail"):
            text = entry["id"]
            path = ctx.paths.thumbs / text[:2] / f"{text}.webp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry["thumbnail"]))
            (ctx.paths.previews / text[:2] / f"{text}.webp").unlink(missing_ok=True)
```

- [ ] **Step 7: Run the tests, lint, commit**

Run: Docker command with `tests/unit/test_decode.py tests/integration/test_api_preview.py tests/unit/test_fingerprint_vectors.py` — Expected: pass. Then `uv run pytest -q` on the host (preview tests skip) and `uv run ruff check . && uv run ruff format .`.

```bash
git add visualstore/media/decode.py visualstore/profiles.py visualstore/api/files.py visualstore/jobs/results.py tests/unit/test_decode.py tests/integration/test_api_preview.py
git commit -m "feat: cached 1024 px preview endpoint for the comparison pane

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Comparison key model and the Compare component

**Files:**
- Modify: `web/src/pages/groupKeys.ts`, `web/src/pages/groupKeys.test.ts`, `web/src/styles.css`
- Create: `web/src/components/compareLayout.ts`, `web/src/components/compareLayout.test.ts`, `web/src/components/Compare.tsx`

**Interfaces:**
- `KeyState` gains `mode: CompareMode` (`"side" | "overlay" | "blink"`) and `target: number | null` (index of the member compared against the selected one; `null` = the nearest neighbour).
- Keys: `s`/`o`/`b` set the mode; `n`/`p` move `target` to the next/previous member other than `selected` (wrapping, starting from the given `nearest` index when `target` is null); `" "` (Space) swaps `selected` and `target` when `target` is set; selecting a member (digit, arrows) resets `target` to `null`.
- `applyKey(state, key, nearest: number | null)` — third parameter new, optional.
- `compareTarget(state, nearest)` → `state.target ?? nearest`.
- `layers(mode, tick, opacity)` → `{ stacked: boolean; left: { shown: boolean; opacity: number }; right: { shown: boolean; opacity: number } }`.
- `<Compare name leftId rightId mode />` renders both images with preview → thumbnail fallback and a notice when a preview failed (Review Focus 4).

- [ ] **Step 1: Write the failing key-model tests**

Append to `web/src/pages/groupKeys.test.ts` (extend `base` with `mode: "side", target: null`):

```ts
import { compareTarget } from "./groupKeys";

describe("comparison keys", () => {
  const withMode = { ...base, mode: "side" as const, target: null };

  it("s, o and b switch the mode", () => {
    expect((applyKey(withMode, "o") as KeyState).mode).toBe("overlay");
    expect((applyKey(withMode, "b") as KeyState).mode).toBe("blink");
    expect((applyKey({ ...withMode, mode: "blink" }, "s") as KeyState).mode).toBe("side");
  });

  it("n and p cycle the comparison target over the other members, starting from the nearest", () => {
    const n1 = applyKey(withMode, "n", 2) as KeyState; // nearest is index 2 → next other is index 1
    expect(n1.target).toBe(1);
    const n2 = applyKey(n1, "n", 2) as KeyState;
    expect(n2.target).toBe(2);
    const p = applyKey(withMode, "p", 1) as KeyState; // previous of 1, skipping selected 0 → 2
    expect(p.target).toBe(2);
    expect((applyKey({ ...withMode, members: [base.members[0]] }, "n", null) as KeyState).target).toBeNull();
  });

  it("selecting a member resets the target; Space swaps selected and target", () => {
    const targeted = { ...withMode, target: 2 };
    expect((applyKey(targeted, "2") as KeyState).target).toBeNull();
    expect((applyKey(targeted, "ArrowDown") as KeyState).target).toBeNull();
    const swapped = applyKey(targeted, " ") as KeyState;
    expect(swapped.selected).toBe(2);
    expect(swapped.target).toBe(0);
    expect(applyKey(withMode, " ")).toEqual(withMode);
  });

  it("compareTarget falls back to the nearest neighbour", () => {
    expect(compareTarget(withMode, 1)).toBe(1);
    expect(compareTarget({ ...withMode, target: 2 }, 1)).toBe(2);
    expect(compareTarget(withMode, null)).toBeNull();
  });
});
```

`web/src/components/compareLayout.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { layers } from "./compareLayout";

describe("layers", () => {
  it("side by side shows both, not stacked", () => {
    expect(layers("side", 0, 0.5)).toEqual({ stacked: false, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 1 } });
  });
  it("overlay stacks the right image with the slider opacity", () => {
    expect(layers("overlay", 7, 0.3)).toEqual({ stacked: true, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 0.3 } });
  });
  it("blink alternates with the tick", () => {
    expect(layers("blink", 0, 0.5).left.shown).toBe(true);
    expect(layers("blink", 0, 0.5).right.shown).toBe(false);
    expect(layers("blink", 1, 0.5).left.shown).toBe(false);
    expect(layers("blink", 1, 0.5).right.shown).toBe(true);
    expect(layers("blink", 1, 0.5).stacked).toBe(true);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- --run` — Expected: FAIL (type errors / missing module).

- [ ] **Step 3: Implement the key model**

Replace `web/src/pages/groupKeys.ts`:

```ts
import type { Decision } from "../types";

export type CompareMode = "side" | "overlay" | "blink";

export interface KeyMember {
  file_id: string;
  decision: Decision;
}

export interface KeyState {
  members: KeyMember[];
  selected: number;
  anchor: number | null;
  mode: CompareMode;
  target: number | null; // member compared against the selected one; null = nearest neighbour
}

export type KeyAction = { action: "confirm" | "skip" | "unrelated" };

function withDecision(state: KeyState, decision: Decision): KeyState {
  return {
    ...state,
    members: state.members.map((m, i) => (i === state.selected ? { ...m, decision } : m)),
  };
}

export function compareTarget(state: KeyState, nearest: number | null): number | null {
  return state.target ?? nearest;
}

function cycleTarget(state: KeyState, nearest: number | null, step: 1 | -1): KeyState {
  const n = state.members.length;
  if (n < 2) return state;
  const start = compareTarget(state, nearest) ?? state.selected;
  let index = start;
  for (let i = 0; i < n; i++) {
    index = (index + step + n) % n;
    if (index !== state.selected) return { ...state, target: index };
  }
  return state;
}

export function applyKey(state: KeyState, key: string, nearest: number | null = null): KeyState | KeyAction {
  if (/^[1-9]$/.test(key)) {
    const index = Number(key) - 1;
    return index < state.members.length ? { ...state, selected: index, target: null } : state;
  }
  switch (key) {
    case "ArrowDown":
      return { ...state, selected: Math.min(state.selected + 1, state.members.length - 1), target: null };
    case "ArrowUp":
      return { ...state, selected: Math.max(state.selected - 1, 0), target: null };
    case "k":
      return withDecision(state, "keep");
    case "d":
      return withDecision(state, "delete");
    case "a":
      return { ...state, anchor: state.selected };
    case "u":
      return state.anchor !== null && state.anchor !== state.selected ? { action: "unrelated" } : state;
    case "s":
      return { ...state, mode: "side" };
    case "o":
      return { ...state, mode: "overlay" };
    case "b":
      return { ...state, mode: "blink" };
    case "n":
      return cycleTarget(state, nearest, 1);
    case "p":
      return cycleTarget(state, nearest, -1);
    case " ":
      return state.target === null ? state : { ...state, selected: state.target, target: state.selected };
    case "Enter":
      return state.members.every((m) => m.decision !== "undecided") ? { action: "confirm" } : state;
    case "Escape":
      return { action: "skip" };
    default:
      return state;
  }
}
```

Update the existing `base` in `groupKeys.test.ts` to include `mode: "side", target: null` (the first `describe` keeps passing; `toEqual(base)` comparisons stay valid).

`web/src/components/compareLayout.ts`:

```ts
import type { CompareMode } from "../pages/groupKeys";

export interface Layer {
  shown: boolean;
  opacity: number;
}

export interface Layers {
  stacked: boolean;
  left: Layer;
  right: Layer;
}

export function layers(mode: CompareMode, tick: number, opacity: number): Layers {
  if (mode === "side") return { stacked: false, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 1 } };
  if (mode === "overlay") return { stacked: true, left: { shown: true, opacity: 1 }, right: { shown: true, opacity } };
  const even = tick % 2 === 0;
  return { stacked: true, left: { shown: even, opacity: 1 }, right: { shown: !even, opacity: 1 } };
}
```

- [ ] **Step 4: Implement the component**

`web/src/components/Compare.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { CompareMode } from "../pages/groupKeys";
import { layers } from "./compareLayout";

function PreviewImage({ name, fileId, opacity, onFallback }: { name: string; fileId: string; opacity: number; onFallback: (id: string) => void }) {
  const [source, setSource] = useState<"preview" | "thumbnail" | "none">("preview");
  useEffect(() => setSource("preview"), [fileId]);
  if (source === "none") return <div className="preview preview-missing">No image available</div>;
  const path = source === "preview" ? "preview" : "thumbnail";
  return (
    <img
      className={`preview preview-${source}`}
      alt=""
      style={{ opacity }}
      src={`/api/p/${name}/files/${fileId}/${path}`}
      onError={() => {
        if (source === "preview") {
          onFallback(fileId);
          setSource("thumbnail");
        } else setSource("none");
      }}
    />
  );
}

export default function Compare({ name, leftId, rightId, mode }: { name: string; leftId: string; rightId: string | null; mode: CompareMode }) {
  const [opacity, setOpacity] = useState(0.5);
  const [tick, setTick] = useState(0);
  const [fallbacks, setFallbacks] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (mode !== "blink") return;
    const timer = window.setInterval(() => setTick((t) => t + 1), 500);
    return () => window.clearInterval(timer);
  }, [mode]);
  const l = layers(mode, tick, opacity);
  const note = (id: string) => setFallbacks((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  const sides = [leftId, rightId].filter((id): id is string => id !== null);
  return (
    <div className={`compare compare-${mode}${l.stacked ? " compare-stacked" : ""}`} data-tick={tick}>
      <div className="compare-images">
        {l.left.shown && <PreviewImage name={name} fileId={leftId} opacity={l.left.opacity} onFallback={note} />}
        {rightId && l.right.shown && <PreviewImage name={name} fileId={rightId} opacity={l.right.opacity} onFallback={note} />}
      </div>
      {mode === "overlay" && rightId && (
        <label className="inline-label">
          Overlay opacity
          <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
        </label>
      )}
      {!rightId && <p className="muted">No other member to compare with.</p>}
      {sides.some((id) => fallbacks.has(id)) && (
        <p className="muted" role="note">
          The original of {sides.filter((id) => fallbacks.has(id)).length === 2 ? "both files" : "one file"} is not available; showing the thumbnail instead.
        </p>
      )}
    </div>
  );
}
```

Append to `web/src/styles.css`:

```css
.compare { margin: 0.75rem 0; }
.compare-images { display: flex; gap: 0.5rem; align-items: flex-start; }
.compare-images img.preview { max-width: 48%; max-height: 70vh; object-fit: contain; background: color-mix(in srgb, currentColor 10%, transparent); }
.compare-stacked .compare-images { position: relative; display: block; min-height: 12rem; }
.compare-stacked .compare-images img.preview { position: absolute; top: 0; left: 0; max-width: 100%; }
.preview-missing { padding: 2rem; opacity: 0.7; }
```

- [ ] **Step 5: Run the frontend checks, commit**

Run: `cd web && npm run lint && npm test -- --run` — Expected: pass (the `GroupDetail` page still compiles because it builds `KeyState` objects — update its `setKeys({ members, selected: 0, anchor: null, mode: "side", target: null })` call in Task 4; if `tsc` fails here on that object literal, add the two fields now in `GroupDetail.tsx` and keep the rest for Task 4).

```bash
git add web/src/pages/groupKeys.ts web/src/pages/groupKeys.test.ts web/src/components/compareLayout.ts web/src/components/compareLayout.test.ts web/src/components/Compare.tsx web/src/styles.css web/src/pages/GroupDetail.tsx
git commit -m "feat(web): comparison modes, target cycling and the Compare component

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Group detail with the comparison pane

**Files:**
- Modify: `web/src/pages/GroupDetail.tsx`, `web/e2e/smoke.spec.ts`

**Interfaces:**
- Page layout: heading; keys help; two columns: left `ol.members` (as today, each row gains `nearest 0.05` from `detail.neighbours` and a "details" link to `/p/{name}/files/{id}` — the route arrives in Task 5; the link is harmless before), centre `<Compare>` of the selected member against `compareTarget(keys, nearestIndex)`, with mode buttons (`Side by side` / `Overlay` / `Blink`) and `Next (n)` / `Previous (p)` / `Swap (Space)` buttons.
- Keys use `applyKey(keys, e.key, nearestIndex)`; the keydown handler ignores events from `INPUT`, `SELECT`, `TEXTAREA`.

- [ ] **Step 1: Implement the page changes**

In `web/src/pages/GroupDetail.tsx`:

1. Imports: `import Compare from "../components/Compare";` and `import { applyKey, compareTarget, type KeyState } from "./groupKeys";`.
2. In `load`, build the key state with `mode: "side", target: null`.
3. Derive after `const { group } = detail;`:

```tsx
  const selectedId = group.members[keys.selected].file_id;
  const nearestId = detail.neighbours[selectedId]?.file_id ?? null;
  const nearestIndex = nearestId ? group.members.findIndex((m) => m.file_id === nearestId) : null;
  const targetIndex = compareTarget(keys, nearestIndex === -1 ? null : nearestIndex);
  const targetId = targetIndex === null ? null : group.members[targetIndex].file_id;
```

4. The keydown effect passes `nearestIndex` — because the effect closes over `keys` only, compute `nearestIndex` inside a `useCallback` or recompute inside the handler from `detail`:

```tsx
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (!keys || !detail || tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      const id = detail.group.members[keys.selected].file_id;
      const near = detail.neighbours[id]?.file_id;
      const nearIndex = near ? detail.group.members.findIndex((m) => m.file_id === near) : -1;
      const result = applyKey(keys, e.key, nearIndex === -1 ? null : nearIndex);
      if ("action" in result) {
        e.preventDefault();
        if (result.action === "confirm") void confirm();
        else if (result.action === "skip") void goNext();
        else void markUnrelated();
      } else if (result !== keys) {
        e.preventDefault();
        setKeys(result);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keys, detail, confirm, goNext, markUnrelated]);
```

5. Keys help line becomes: `Keys: 1–9 select · ↑/↓ move · k keep · d delete · a anchor · u mark anchor+selected unrelated · s/o/b side-by-side/overlay/blink · n/p other member · Space swap · Enter confirm · Esc skip`.
6. Each member's first `<div>` gains, after the size: `{detail.neighbours[m.file_id] && <span className="muted"> · nearest {detail.neighbours[m.file_id]!.distance.toFixed(3)}</span>}` and a link `<Link to={`/p/${name}/files/${m.file_id}`}>details</Link>` (import `Link` from react-router-dom).
7. Wrap the members list and the pane in `<div className="detail-columns">`; the pane:

```tsx
        <div className="pane">
          <div className="actions-row">
            {(["side", "overlay", "blink"] as const).map((m) => (
              <button key={m} type="button" className={keys.mode === m ? "active" : ""} onClick={() => setKeys({ ...keys, mode: m })}>
                {m === "side" ? "Side by side (s)" : m === "overlay" ? "Overlay (o)" : "Blink (b)"}
              </button>
            ))}
            <button type="button" onClick={() => setKeys(applyKey(keys, "n", nearestIndex === -1 ? null : nearestIndex) as KeyState)} disabled={group.members.length < 2}>
              Next (n)
            </button>
            <button type="button" onClick={() => setKeys(applyKey(keys, "p", nearestIndex === -1 ? null : nearestIndex) as KeyState)} disabled={group.members.length < 2}>
              Previous (p)
            </button>
            <button type="button" onClick={() => setKeys(applyKey(keys, " ") as KeyState)} disabled={keys.target === null}>
              Swap (Space)
            </button>
          </div>
          <p className="muted">
            Comparing <code>{group.members[keys.selected].relative_path}</code>
            {targetId && (
              <>
                {" "}with <code>{group.members[targetIndex!].relative_path}</code>
                {keys.target === null ? " (nearest neighbour)" : ""}
              </>
            )}
          </p>
          <Compare name={name} leftId={selectedId} rightId={targetId} mode={keys.mode} />
        </div>
```

Append to `styles.css`: `.detail-columns { display: grid; grid-template-columns: minmax(18rem, 2fr) 3fr; gap: 1rem; align-items: start; } @media (max-width: 900px) { .detail-columns { grid-template-columns: 1fr; } }`.

Also widen `main` for this page: in `styles.css` change `main { max-width: 960px; ... }` to `max-width: 1400px`.

- [ ] **Step 2: Extend the e2e flow**

In `web/e2e/smoke.spec.ts`, after `await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");` insert:

```ts
  const previews = page.locator("img.preview");
  await expect(previews).toHaveCount(2);
  await expect.poll(() => previews.first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("o");
  await expect(page.getByLabel("Overlay opacity")).toBeVisible();
  await page.keyboard.press("b");
  await expect(page.locator(".compare-blink")).toBeVisible();
  await page.keyboard.press("s");
  await expect(page.locator(".compare-side img.preview")).toHaveCount(2);
```

- [ ] **Step 3: Run the frontend checks and the e2e, commit**

Run: `cd web && npm run lint && npm test -- --run && npm run build && npm run e2e` — Expected: all pass (2 e2e tests).

```bash
git add web/src/pages/GroupDetail.tsx web/src/styles.css web/e2e/smoke.spec.ts
git commit -m "feat(web): comparison pane with nearest neighbour, modes and target keys on group detail

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: File detail page with candidate pairs

**Files:**
- Modify: `visualstore/api/files.py`, `tests/integration/test_api_groups.py`, `web/src/types.ts`, `web/src/App.tsx`
- Create: `web/src/pages/FileDetail.tsx`

**Interfaces:**
- `GET /api/p/{name}/files/{id}` gains `"media_type": "image" | "animated" | "video" | "other"`, `"folder_name": str`, `"group_id"` (already), and `"candidates": [{"file_id", "distance", "judgment", "relative_path", "folder_name", "group_id": str | null}]` — vector pairs with `group_distance < distance ≤ candidate_distance` and `judgment != "unrelated"`, sorted by distance (Review Focus 5).
- Frontend route `/p/:name/files/:fileId` → `FileDetail`.

- [ ] **Step 1: Write the failing API test**

Append to `tests/integration/test_api_groups.py`:

```python
def test_file_detail_lists_candidates_beyond_the_grouping_threshold(client, seeded):
    """Review Focus 5."""
    s = seeded
    conn = client.app.state.vs.registry.conn("a")
    add_extra = lambda name: files.insert_file(  # noqa: E731
        conn, folder_id=s["inbox"], directory="", name=name, extension_id=folders.extension_map(conn)["jpg"]["id"], size=1, mtime=1
    )
    near, cand, far, bad = add_extra("near.jpg"), add_extra("cand.jpg"), add_extra("far.jpg"), add_extra("bad.jpg")
    a = s["a"]
    pairs.upsert_vector_pair(conn, a, near, 0.1, "m")  # within group_distance (0.3): grouped, not a candidate
    pairs.upsert_vector_pair(conn, a, cand, 0.4, "m")  # candidate
    pairs.upsert_vector_pair(conn, a, far, 0.6, "m")  # beyond candidate_distance (0.5)
    pairs.upsert_vector_pair(conn, a, bad, 0.35, "m")
    pairs.set_judgment(conn, a, bad, "unrelated", "user")
    d = client.get(f"/api/p/a/files/{id_to_str(a)}").json()
    assert d["media_type"] == "image" and d["folder_name"] == "Arch"
    assert [c["file_id"] for c in d["candidates"]] == [id_to_str(cand)]
    assert d["candidates"][0]["relative_path"] == "cand.jpg" and d["candidates"][0]["distance"] == 0.4
    assert d["candidates"][0]["group_id"] is None and d["group_id"] == id_to_str(s["gid"])
```

Run: `uv run pytest tests/integration/test_api_groups.py -q -k candidates` — Expected: FAIL (`KeyError: 'media_type'`).

- [ ] **Step 2: Implement**

Replace `file_detail` in `visualstore/api/files.py`:

```python
@router.get("/{file_id}")
async def file_detail(file_id: str, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    d = files_db.file_to_dict(row, folder["path"])
    d["folder_name"] = folder["name"]
    d["media_type"] = _media_type(conn, bytes(row["extension_id"]))
    g = groups_db.group_of_file(conn, fid)
    d["group_id"] = id_to_str(bytes(g["id"])) if g else None
    rows = pairs_db.pairs_of_file(conn, fid)
    d["pairs"] = [pairs_db.pair_to_dict(p) for p in rows]
    settings = settings_db.all(conn)
    low = float(settings["grouping.group_distance"])
    high = float(settings["similarity.candidate_distance"])
    candidates = []
    for p in rows:
        if p["source"] != "vector" or p["judgment"] == "unrelated" or p["distance"] is None:
            continue
        if not (low < p["distance"] <= high):
            continue
        other = bytes(p["file_b"]) if bytes(p["file_a"]) == fid else bytes(p["file_a"])
        orow = files_db.get_file(conn, other)
        if orow is None:
            continue
        ofolder = folders_db.get_folder(conn, bytes(orow["folder_id"]))
        og = groups_db.group_of_file(conn, other)
        candidates.append(
            {
                "file_id": id_to_str(other),
                "distance": p["distance"],
                "judgment": p["judgment"],
                "relative_path": files_db.relative_path(orow["directory"], orow["name"]),
                "folder_name": ofolder["name"],
                "group_id": id_to_str(bytes(og["id"])) if og else None,
            }
        )
    d["candidates"] = sorted(candidates, key=lambda c: c["distance"])
    return d
```

Types in `web/src/types.ts`:

```ts
export interface Candidate {
  file_id: string;
  distance: number;
  judgment: Pair["judgment"];
  relative_path: string;
  folder_name: string;
  group_id: string | null;
}

export interface FileDetail {
  id: string;
  folder_id: string;
  folder_name: string;
  directory: string;
  name: string;
  size: number;
  mtime: number;
  sha1: string | null;
  state: string;
  media_type: "image" | "animated" | "video" | "other";
  relative_path: string;
  absolute_path: string;
  group_id: string | null;
  pairs: Pair[];
  candidates: Candidate[];
}
```

`web/src/pages/FileDetail.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get, put } from "../api";
import Thumb from "../components/Thumb";
import { formatBytes } from "../format";
import type { FileDetail as Detail } from "../types";

export default function FileDetail() {
  const { name = "", fileId = "" } = useParams();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await get<Detail>(`/api/p/${name}/files/${fileId}`));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the file.");
    }
  }, [name, fileId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function judge(other: string, judgment: "related_keep" | "unrelated") {
    setError(null);
    try {
      await put(`/api/p/${name}/pairs/${fileId}/${other}/judgment`, { judgment });
      setNotice(judgment === "unrelated" ? "Marked unrelated; groups are being recomputed." : "Marked related; both files are kept.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not record the judgment.");
    }
  }

  if (error && !detail)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!detail) return <p className="muted">Loading…</p>;

  return (
    <section>
      <h1>
        <code>{detail.relative_path}</code>
      </h1>
      <div className="file-head">
        <Thumb name={name} fileId={detail.id} size={160} />
        <dl>
          <dt>Folder</dt>
          <dd>{detail.folder_name}</dd>
          <dt>Size</dt>
          <dd>{formatBytes(detail.size)}</dd>
          <dt>Type</dt>
          <dd>{detail.media_type}</dd>
          <dt>State</dt>
          <dd>{detail.state}</dd>
          <dt>SHA-1</dt>
          <dd>
            <code>{detail.sha1 ?? "—"}</code>
          </dd>
          <dt>Group</dt>
          <dd>{detail.group_id ? <Link to={`/p/${name}/groups/${detail.group_id}`}>open group</Link> : "none"}</dd>
        </dl>
      </div>
      <p>
        <a href={`/api/p/${name}/files/${detail.id}/content`} target="_blank" rel="noreferrer">
          Open original
        </a>
      </p>

      <h2>Candidate pairs</h2>
      <p className="muted">Similar beyond the grouping threshold but within the candidate distance. Judging a pair changes the groups.</p>
      {detail.candidates.length === 0 ? (
        <p className="muted">No candidates.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Distance</th>
              <th>Judgment</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {detail.candidates.map((c) => (
              <tr key={c.file_id}>
                <td className="with-thumb">
                  <Thumb name={name} fileId={c.file_id} />
                  <Link to={`/p/${name}/files/${c.file_id}`}>{c.relative_path}</Link> <span className="muted">· {c.folder_name}</span>
                </td>
                <td>{c.distance.toFixed(3)}</td>
                <td>{c.judgment.replace("_", " ")}</td>
                <td className="actions">
                  <button type="button" onClick={() => void judge(c.file_id, "related_keep")} disabled={c.judgment === "related_keep"}>
                    Related, keep both
                  </button>
                  <button type="button" className="danger" onClick={() => void judge(c.file_id, "unrelated")}>
                    Unrelated
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {notice && (
        <p className="muted" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
```

`App.tsx`: `import FileDetail from "./pages/FileDetail";` and `<Route path="/p/:name/files/:fileId" element={<FileDetail />} />`.

`styles.css`: `.file-head { display: flex; gap: 1rem; align-items: flex-start; } .file-head dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem; margin: 0; } .file-head dt { opacity: 0.7; }`.

- [ ] **Step 3: Run the tests, lint, commit**

Run: `uv run pytest tests/integration/test_api_groups.py -q && uv run ruff check . && uv run ruff format . && (cd web && npm run lint && npm test -- --run && npm run build)` — Expected: pass.

```bash
git add visualstore/api/files.py tests/integration/test_api_groups.py web/src/types.ts web/src/App.tsx web/src/pages/FileDetail.tsx web/src/styles.css
git commit -m "feat: file detail with candidate pairs and judgment buttons

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Bulk rules — plan, preview, execute job

**Files:**
- Create: `visualstore/domain/bulk.py`, `visualstore/jobs/resolve.py`, `visualstore/jobs/bulkplan.py`, `visualstore/jobs/io/bulk_resolve.py`, `visualstore/api/bulk.py`, `tests/unit/test_domain_bulk.py`, `tests/integration/test_bulk_resolve.py`
- Modify: `visualstore/db/settings.py`, `visualstore/db/pairs.py`, `visualstore/api/groups.py`, `visualstore/jobs/io/actions.py`, `visualstore/jobs/io/__init__.py`, `visualstore/jobs/types.py`, `visualstore/app.py`

**Interfaces:**
- Setting `bulk.confirm_threshold: int = 100`.
- `pairs_db.inner_pairs(conn, member_ids: set[bytes]) -> list[Row]` (moved from `api/groups._inner_pairs`, which now delegates).
- `domain/bulk.py`: `signature(member_ids: Iterable[bytes]) -> str` (sha1 hex of the sorted ids); `GroupPlan(group_id, signature, delete: tuple[bytes, ...], keep: tuple[bytes, ...], folders: tuple[str, ...])`; `plan_group(group_id, members: Sequence[Member], folder_names: dict[bytes, str], all_identical: bool) -> GroupPlan | None` (None when the rules leave anything undecided or delete nothing); `BulkPlan(groups, matched, skipped, files_to_delete, folders, token)`; `build_plan(matched: int, plans: Iterable[GroupPlan | None]) -> BulkPlan` (token = sha1 over `group_id.hex():signature` lines in group-id order).
- `jobs/resolve.py`: `commit_decisions(conn, group_id, decisions: dict[bytes, str]) -> list[bytes]` — stores decisions, marks `none` pairs among kept members `related_keep`, returns the ids to trash (status is set by the caller: `resolved` when nothing to trash, else by `apply_action`).
- `jobs/bulkplan.py`: `plan_for_group(conn, row) -> GroupPlan | None`; `plan_for_filter(conn, settings, *, band, folder_id, has_identical, min_size) -> BulkPlan` over `status = open` groups.
- `jobs/io/actions.py`: `execute_action(conn, vconn, ctx, params: dict, now) -> dict` (the body of `run_apply_action`, which now opens the connections and delegates).
- Io job `bulk_resolve`, params `{"groups": [{"group_id": str, "signature": str, "delete": [str]}]}` → result `{"resolved": int, "trashed": int, "skipped": [{"group_id", "reason"}]}`.
- `POST /api/p/{name}/groups/bulk/preview {band?, folder_id?, has_identical?, min_size?}` → `{"matched", "planned", "skipped", "files_to_delete", "folders", "token", "threshold", "typed_confirmation_required"}`.
- `POST /api/p/{name}/groups/bulk/execute {…filter, "token": str, "confirm": int | null}` → 201 job; 400 when nothing is planned (Review Focus 1) or the typed count is missing/wrong; 409 when the token no longer matches (Review Focus 2).

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_domain_bulk.py`:

```python
from visualstore.domain.bulk import build_plan, plan_group, signature
from visualstore.domain.resolution import Member

G = b"g" * 16
ARCH, INBOX, ARCH2 = b"A" * 16, b"I" * 16, b"B" * 16
NAMES = {ARCH: "Arch", INBOX: "Inbox", ARCH2: "Arch2"}


def m(fid, folder, role, path, priority=0):
    return Member(fid, folder, role, priority, path)


def test_signature_is_order_independent():
    assert signature([b"b" * 16, b"a" * 16]) == signature([b"a" * 16, b"b" * 16])
    assert signature([b"a" * 16]) != signature([b"b" * 16])


def test_archive_plus_inbox_deletes_the_inbox_files():
    members = [m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, INBOX, "inbox", "b.jpg"), m(b"3" * 16, INBOX, "inbox", "c.jpg")]
    plan = plan_group(G, members, NAMES, all_identical=False)
    assert plan is not None
    assert set(plan.delete) == {b"2" * 16, b"3" * 16} and plan.keep == (b"1" * 16,)
    assert plan.folders == ("Inbox",) and plan.signature == signature([b"1" * 16, b"2" * 16, b"3" * 16])


def test_inbox_only_keeps_the_first_by_path_and_archive_only_is_skipped():
    inbox = [m(b"2" * 16, INBOX, "inbox", "z.jpg"), m(b"1" * 16, INBOX, "inbox", "a.jpg")]
    plan = plan_group(G, inbox, NAMES, all_identical=True)
    assert plan is not None and plan.keep == (b"1" * 16,) and plan.delete == (b"2" * 16,)
    archive = [m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, ARCH, "archive", "b.jpg")]
    assert plan_group(G, archive, NAMES, all_identical=True) is None  # undecided: skipped
    two_archives = [m(b"1" * 16, ARCH, "archive", "a.jpg", 1), m(b"2" * 16, ARCH2, "archive", "b.jpg", 2)]
    plan = plan_group(G, two_archives, NAMES, all_identical=True)
    assert plan is not None and plan.delete == (b"2" * 16,) and plan.folders == ("Arch2",)


def test_build_plan_counts_and_tokens():
    p1 = plan_group(b"1" * 16, [m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, INBOX, "inbox", "b.jpg")], NAMES, all_identical=False)
    p2 = plan_group(b"2" * 16, [m(b"3" * 16, INBOX, "inbox", "c.jpg"), m(b"4" * 16, INBOX, "inbox", "d.jpg")], NAMES, all_identical=False)
    plan = build_plan(3, [p1, None, p2])
    assert plan.matched == 3 and plan.skipped == 1 and len(plan.groups) == 2
    assert plan.files_to_delete == 2 and plan.folders == ("Inbox",)
    assert plan.token == build_plan(3, [p2, None, p1]).token  # order independent
    assert plan.token != build_plan(3, [p1]).token
    empty = build_plan(0, [])
    assert empty.groups == () and empty.files_to_delete == 0 and empty.token
```

Run: `uv run pytest tests/unit/test_domain_bulk.py -q` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement the pure module and the shared helpers**

`visualstore/domain/bulk.py`:

```python
"""Bulk application of the default rules over a queue filter (design §8.4). Pure."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from visualstore.domain.resolution import Member, preselect


def signature(member_ids: Iterable[bytes]) -> str:
    """Identifies a group's member set so execution can detect a group that changed."""
    return hashlib.sha1(b"".join(sorted(member_ids))).hexdigest()


@dataclass(frozen=True)
class GroupPlan:
    group_id: bytes
    signature: str
    delete: tuple[bytes, ...]
    keep: tuple[bytes, ...]
    folders: tuple[str, ...]  # names of the Folders that lose files


@dataclass(frozen=True)
class BulkPlan:
    groups: tuple[GroupPlan, ...]
    matched: int
    skipped: int
    files_to_delete: int
    folders: tuple[str, ...]
    token: str


def plan_group(
    group_id: bytes,
    members: Sequence[Member],
    folder_names: dict[bytes, str],
    *,
    all_identical: bool,
) -> GroupPlan | None:
    """The default resolution for one group, or None when the rules leave a member undecided or
    nothing to delete: such groups are skipped by a bulk run (Global Constraints)."""
    pre = preselect(members, all_identical=all_identical, auto_execute_identical=False)
    if any(d == "undecided" for d in pre.decisions.values()):
        return None
    order = sorted(members, key=lambda m: m.relative_path)
    delete = tuple(m.file_id for m in order if pre.decisions[m.file_id] == "delete")
    keep = tuple(m.file_id for m in order if pre.decisions[m.file_id] == "keep")
    if not delete:
        return None
    folders = tuple(
        sorted({folder_names[m.folder_id] for m in members if pre.decisions[m.file_id] == "delete"})
    )
    return GroupPlan(group_id, signature(m.file_id for m in members), delete, keep, folders)


def build_plan(matched: int, plans: Iterable[GroupPlan | None]) -> BulkPlan:
    plans = list(plans)
    groups = tuple(sorted((p for p in plans if p is not None), key=lambda p: p.group_id))
    token_source = "\n".join(f"{p.group_id.hex()}:{p.signature}" for p in groups)
    return BulkPlan(
        groups=groups,
        matched=matched,
        skipped=sum(1 for p in plans if p is None),
        files_to_delete=sum(len(p.delete) for p in groups),
        folders=tuple(sorted({f for p in groups for f in p.folders})),
        token=hashlib.sha1(token_source.encode()).hexdigest(),
    )
```

`visualstore/db/settings.py`: add `"bulk.confirm_threshold": 100,` to `DEFAULTS`.

`visualstore/db/pairs.py`: add

```python
def inner_pairs(conn: sqlite3.Connection, member_ids: set[bytes]) -> list[sqlite3.Row]:
    """Pairs whose both ends are in `member_ids`."""
    return [
        p
        for p in edges_touching(conn, member_ids)
        if bytes(p["file_a"]) in member_ids and bytes(p["file_b"]) in member_ids
    ]
```

and make `api/groups._inner_pairs` return `pairs_db.inner_pairs(conn, member_ids)`.

`visualstore/jobs/resolve.py`:

```python
"""Resolution of one group (design §8.3 "Confirming"), shared by the API and the bulk job."""

from __future__ import annotations

import sqlite3

from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db


def commit_decisions(
    conn: sqlite3.Connection, group_id: bytes, decisions: dict[bytes, str]
) -> list[bytes]:
    """Store keep/delete decisions and mark pairs among kept members related_keep.
    Returns the file ids to trash; the caller enqueues apply_action for them or, when the
    list is empty, marks the group resolved."""
    groups_db.set_decisions(conn, group_id, decisions)
    kept = {f for f, d in decisions.items() if d == "keep"}
    for p in pairs_db.inner_pairs(conn, kept):
        if p["judgment"] == "none":
            pairs_db.set_judgment(
                conn, bytes(p["file_a"]), bytes(p["file_b"]), "related_keep", "user"
            )
    return [f for f, d in decisions.items() if d == "delete"]
```

In `visualstore/api/groups.py`, `resolve_group` becomes:

```python
    groups_db.set_decisions  # (remove the old inline block; keep validation above)
    trash_ids = commit_decisions(conn, gid, {m: decisions[m] for m in members})
    trash = [id_to_str(m) for m in trash_ids]
    job = None
    if trash:
        job_id = jobs_db.enqueue(conn, type="apply_action", kind="io", priority=PRIORITY_USER, params={"group_id": group_id, "trash": trash})
        job = jobs_db.to_dict(jobs_db.get(conn, job_id))
    else:
        groups_db.set_status(conn, gid, "resolved")
    return {"group": _group_out(conn, groups_db.get_group(conn, gid)), "job": job}
```

(with `from visualstore.jobs.resolve import commit_decisions`; delete the stray `groups_db.set_decisions` line — it is shown only to mark where the old block was).

`visualstore/jobs/bulkplan.py`:

```python
"""Default-rule plans computed from the database (design §8.4). The pure parts are in
visualstore.domain.bulk; this module reads groups and members."""

from __future__ import annotations

import sqlite3

from visualstore.db import groups as groups_db
from visualstore.db import pairs as pairs_db
from visualstore.domain.bands import band_bounds, thresholds_from
from visualstore.domain.bulk import BulkPlan, GroupPlan, build_plan, plan_group
from visualstore.domain.resolution import Member

MAX_GROUPS = 100_000


def plan_for_group(conn: sqlite3.Connection, row: sqlite3.Row) -> GroupPlan | None:
    gid = bytes(row["id"])
    rows = groups_db.members(conn, gid)
    members = [
        Member(
            bytes(r["file_id"]),
            bytes(r["folder_id"]),
            r["role"],
            r["folder_priority"],
            groups_db.member_to_dict(r)["relative_path"],
        )
        for r in rows
    ]
    names = {bytes(r["folder_id"]): r["folder_name"] for r in rows}
    inner = [
        p
        for p in pairs_db.inner_pairs(conn, {m.file_id for m in members})
        if p["judgment"] != "unrelated"
    ]
    all_identical = bool(inner) and all(p["source"] == "sha1" for p in inner)
    return plan_group(gid, members, names, all_identical=all_identical)


def plan_for_filter(
    conn: sqlite3.Connection,
    settings: dict,
    *,
    band: str | None,
    folder_id: bytes | None,
    has_identical: bool | None,
    min_size: int | None,
) -> BulkPlan:
    gt = le = None
    if band == "identical":
        has_identical = True
    elif band is not None:
        has_identical = False
        lo, hi = band_bounds(band, thresholds_from(settings))
        gt = None if lo == float("-inf") else lo
        le = None if hi == float("inf") else hi
    rows = groups_db.list_groups(
        conn,
        status="open",
        folder_id=folder_id,
        has_identical=has_identical,
        min_size=min_size,
        min_distance_gt=gt,
        min_distance_le=le,
        limit=MAX_GROUPS,
        offset=0,
    )
    return build_plan(len(rows), [plan_for_group(conn, r) for r in rows])
```

- [ ] **Step 3: Run the unit tests, then write the failing integration tests**

Run: `uv run pytest tests/unit/test_domain_bulk.py tests/integration/test_api_groups.py -q` — Expected: pass (the resolve endpoint still behaves the same).

`tests/integration/test_bulk_resolve.py`:

```python
import hashlib
import json

import pytest

from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.domain.bulk import signature
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.bulk_resolve import run_bulk_resolve
from visualstore.jobs.scheduler import IoContext


@pytest.fixture
def world(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    roots = {k: tmp_path / k for k in ("arch", "inbox", "arch2")}
    for r in roots.values():
        r.mkdir()
    f = {
        "arch": folders.create_folder(conn, name="Arch", path=str(roots["arch"]), role="archive", compare_scope="all", priority=1),
        "inbox": folders.create_folder(conn, name="Inbox", path=str(roots["inbox"]), role="inbox", compare_scope="all"),
        "arch2": folders.create_folder(conn, name="Arch2", path=str(roots["arch2"]), role="archive", compare_scope="all", priority=2),
    }
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, name, content):
        (roots[folder] / name).write_bytes(content)
        st = (roots[folder] / name).stat()
        fid = files.insert_file(conn, folder_id=f[folder], directory="", name=name, extension_id=ext, size=st.st_size, mtime=st.st_mtime_ns)
        files.set_fingerprint_result(conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted")
        return fid

    def group(ids, identical, distance):
        hub = ids[0]
        for other in ids[1:]:
            if identical:
                pairs.upsert_sha1_pair(conn, hub, other)
            else:
                pairs.upsert_vector_pair(conn, hub, other, distance, "m")
        [gid] = groups.replace_groups(conn, [], [NewGroup(frozenset(ids), 0.0 if identical else distance, identical, "open", dict.fromkeys(ids, "undecided"))])
        return gid

    a1, i1, i2 = add("arch", "a1.jpg", b"x1"), add("inbox", "i1.jpg", b"x1"), add("inbox", "i2.jpg", b"x1")
    g_mixed = group([a1, i1, i2], True, 0.0)  # archive + inbox, identical: delete i1, i2
    i3, i4 = add("inbox", "i3.jpg", b"y1"), add("inbox", "i4.jpg", b"y2")
    g_inbox = group([i3, i4], False, 0.1)  # inbox only, near duplicate: delete i4
    a2, a3 = add("arch", "a2.jpg", b"z1"), add("arch", "a3.jpg", b"z2")
    g_arch = group([a2, a3], False, 0.25)  # archive only, one Folder: skipped
    return {"client": client, "reg": reg, "conn": conn, "roots": roots, "f": f, "ids": {"a1": a1, "i1": i1, "i2": i2, "i3": i3, "i4": i4, "a2": a2, "a3": a3}, "g": {"mixed": g_mixed, "inbox": g_inbox, "arch": g_arch}}


def test_preview_counts_planned_and_skipped_groups(world):
    client = world["client"]
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    assert p["matched"] == 3 and p["planned"] == 2 and p["skipped"] == 1
    assert p["files_to_delete"] == 3 and p["folders"] == ["Inbox"]
    assert p["threshold"] == 100 and p["typed_confirmation_required"] is False and len(p["token"]) == 40
    only_identical = client.post("/api/p/a/groups/bulk/preview", json={"band": "identical"}).json()
    assert only_identical["planned"] == 1 and only_identical["files_to_delete"] == 2
    by_folder = client.post("/api/p/a/groups/bulk/preview", json={"folder_id": id_to_str(world["f"]["arch2"])}).json()
    assert by_folder == {**by_folder, "matched": 0, "planned": 0, "files_to_delete": 0}


def test_execute_refuses_nothing_to_do_stale_tokens_and_missing_confirmation(world):
    """Review Focus 1 and 2."""
    client, conn = world["client"], world["conn"]
    nothing = client.post("/api/p/a/groups/bulk/execute", json={"folder_id": id_to_str(world["f"]["arch2"]), "token": "x"})
    assert nothing.status_code == 400 and "No group" in nothing.json()["detail"]
    assert jobs_db.list_jobs(conn) == []
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    stale = client.post("/api/p/a/groups/bulk/execute", json={"token": "0" * 40})
    assert stale.status_code == 409
    settings_db.set(conn, "bulk.confirm_threshold", 2)
    assert client.post("/api/p/a/groups/bulk/preview", json={}).json()["typed_confirmation_required"] is True
    unconfirmed = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"]})
    assert unconfirmed.status_code == 400 and "type" in unconfirmed.json()["detail"]
    wrong = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"], "confirm": 2})
    assert wrong.status_code == 400
    # the queue changes between preview and execute: the token no longer matches
    pairs.set_judgment(conn, world["ids"]["i3"], world["ids"]["i4"], "unrelated", "user")
    groups.replace_groups(conn, [world["g"]["inbox"]], [])
    changed = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"], "confirm": 3})
    assert changed.status_code == 409
    assert jobs_db.list_jobs(conn) == []


def test_execute_runs_one_job_that_trashes_and_resolves_each_planned_group(world):
    client, conn, roots, ids = world["client"], world["conn"], world["roots"], world["ids"]
    p = client.post("/api/p/a/groups/bulk/preview", json={}).json()
    job = client.post("/api/p/a/groups/bulk/execute", json={"token": p["token"]})
    assert job.status_code == 201 and job.json()["type"] == "bulk_resolve"
    done = wait_for(client, "a", job.json()["id"])
    assert done["state"] == "done", done
    result = done["progress"]["result"]
    assert result["resolved"] == 2 and result["trashed"] == 3 and result["skipped"] == []
    assert groups.get_group(conn, world["g"]["mixed"])["status"] == "resolved"
    assert groups.get_group(conn, world["g"]["inbox"])["status"] == "resolved"
    assert groups.get_group(conn, world["g"]["arch"])["status"] == "open"
    assert not (roots["inbox"] / "i1.jpg").exists() and not (roots["inbox"] / "i4.jpg").exists()
    assert (roots["inbox"] / "i3.jpg").exists() and (roots["arch"] / "a1.jpg").exists()
    assert files.get_file(conn, ids["i4"]) is None and files.get_file(conn, ids["i3"]) is not None
    assert IO_HANDLERS["bulk_resolve"] is run_bulk_resolve


def test_job_skips_groups_whose_members_or_rules_changed(world):
    """Review Focus 2 at execution time."""
    reg, conn, ids, g = world["reg"], world["conn"], world["ids"], world["g"]
    stale_sig = signature([ids["i3"], ids["i4"]])
    i5 = files.insert_file(conn, folder_id=world["f"]["inbox"], directory="", name="i5.jpg", extension_id=folders.extension_map(conn)["jpg"]["id"], size=1, mtime=1)
    files.set_fingerprint_result(conn, i5, sha1=b"\x09" * 20, state="fingerprinted")
    groups.replace_groups(conn, [g["inbox"]], [NewGroup(frozenset({ids["i3"], ids["i4"], i5}), 0.1, False, "open", {})])
    new_gid = groups.group_of_file(conn, ids["i3"])["id"]
    params = {
        "groups": [
            {"group_id": id_to_str(new_gid), "signature": stale_sig, "delete": [id_to_str(ids["i4"])]},
            {"group_id": id_to_str(g["mixed"]), "signature": signature([ids["a1"], ids["i1"], ids["i2"]]), "delete": [id_to_str(ids["i1"])]},  # planned delete set differs from the rules
            {"group_id": id_to_str(b"\x00" * 16), "signature": "", "delete": []},
        ]
    }
    ctx = IoContext(profile="a", paths=reg.paths("a"), backup_dir=reg.data_dir / "bk", global_db_path=reg.global_db_path, settings=settings_db.all(conn), params=params, report=lambda p: None, model_dir=reg.data_dir / "models")
    result = run_bulk_resolve(ctx)
    assert result["resolved"] == 0 and result["trashed"] == 0
    assert sorted(s["reason"] for s in result["skipped"]) == ["gone", "members changed", "rules changed"]
    assert files.get_file(conn, ids["i4"]) is not None and files.get_file(conn, ids["i1"]) is not None
```

Run: `uv run pytest tests/integration/test_bulk_resolve.py -q` — Expected: FAIL (import error).

- [ ] **Step 4: Implement the job, the API and the `apply_action` refactor**

In `visualstore/jobs/io/actions.py` split `run_apply_action`:

```python
def execute_action(
    conn: sqlite3.Connection, vconn: sqlite3.Connection, ctx: IoContext, params: dict, now: datetime
) -> dict:
    """The body of apply_action for one params dict; the bulk job calls it per group."""
    result: dict = {"trashed": 0, "restored": 0, "skipped": []}
    group_text = params.get("group_id")
    group_id = id_from_str(group_text) if group_text else None
    for text in params.get("trash", []):
        file_id = id_from_str(text)
        reason = _trash_one(conn, ctx, file_id, now, group_id)
        if reason:
            result["skipped"].append({"id": text, "reason": reason})
        else:
            fp.delete_for_files(vconn, [file_id])
            result["trashed"] += 1
    for text in params.get("restore", []):
        reason = _restore_one(conn, ctx, id_from_str(text))
        if reason:
            result["skipped"].append({"id": text, "reason": reason})
        else:
            result["restored"] += 1
    if group_id is not None and not result["skipped"]:
        g = groups_db.get_group(conn, group_id)
        if g is not None and g["status"] == "open":
            groups_db.set_status(conn, bytes(g["id"]), "resolved")
    if result["trashed"] and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
    return result


def run_apply_action(ctx: IoContext) -> dict:
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        return execute_action(conn, vconn, ctx, ctx.params, datetime.now(UTC))
```

`visualstore/jobs/io/bulk_resolve.py`:

```python
"""Bulk resolution job (design §8.4, ADR-0006): apply the planned default decisions to each
group after re-validating it, trash inline, report progress per group."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime

from visualstore.db import groups as groups_db
from visualstore.domain.bulk import signature
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs.bulkplan import plan_for_group
from visualstore.jobs.io.actions import ActionError, execute_action
from visualstore.jobs.resolve import commit_decisions
from visualstore.jobs.scheduler import IoContext


def _check(conn, item: dict) -> tuple[str | None, object]:
    """Returns (skip reason, group row)."""
    gid = id_from_str(item["group_id"])
    row = groups_db.get_group(conn, gid)
    if row is None or row["status"] != "open":
        return "gone", None
    members = {bytes(m["file_id"]) for m in groups_db.members(conn, gid)}
    if signature(members) != item["signature"]:
        return "members changed", row
    plan = plan_for_group(conn, row)
    planned = {id_from_str(t) for t in item["delete"]}
    if plan is None or set(plan.delete) != planned:
        return "rules changed", row
    return None, row


def run_bulk_resolve(ctx: IoContext) -> dict:
    items = ctx.params.get("groups", [])
    result: dict = {"resolved": 0, "trashed": 0, "skipped": []}
    now = datetime.now(UTC)
    with closing(ctx.open_metadata()) as conn, closing(ctx.open_vectors()) as vconn:
        for i, item in enumerate(items, start=1):
            reason, row = _check(conn, item)
            if reason:
                result["skipped"].append({"group_id": item["group_id"], "reason": reason})
            else:
                gid = bytes(row["id"])
                planned = {id_from_str(t) for t in item["delete"]}
                decisions = {
                    bytes(m["file_id"]): ("delete" if bytes(m["file_id"]) in planned else "keep")
                    for m in groups_db.members(conn, gid)
                }
                trash = commit_decisions(conn, gid, decisions)
                try:
                    done = execute_action(
                        conn,
                        vconn,
                        ctx,
                        {"group_id": id_to_str(gid), "trash": [id_to_str(f) for f in trash]},
                        now,
                    )
                except ActionError as exc:
                    result["skipped"].append({"group_id": item["group_id"], "reason": str(exc)})
                    continue
                result["trashed"] += done["trashed"]
                if done["skipped"]:
                    result["skipped"].append(
                        {"group_id": item["group_id"], "reason": f"{len(done['skipped'])} file(s) skipped"}
                    )
                else:
                    result["resolved"] += 1
            ctx.report({"done": i, "total": len(items), "message": f"{result['trashed']} files trashed"})
    return result
```

Register `"bulk_resolve": run_bulk_resolve` in `IO_HANDLERS` (`visualstore/jobs/io/__init__.py`) and `"bulk_resolve": "io"` in `KIND_OF` (`visualstore/jobs/types.py`).

`visualstore/api/bulk.py`:

```python
"""Bulk rules over the review queue (design §8.4, ADR-0006)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from visualstore.api.deps import get_profile, get_state, require_auth
from visualstore.api.ids import parse_id
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.domain.bands import BANDS
from visualstore.domain.bulk import BulkPlan
from visualstore.ids import id_to_str
from visualstore.jobs.bulkplan import plan_for_filter
from visualstore.jobs.types import PRIORITY_USER

router = APIRouter(
    prefix="/api/p/{name}/groups/bulk", tags=["bulk"], dependencies=[Depends(require_auth)]
)


class BulkFilter(BaseModel):
    band: str | None = None
    folder_id: str | None = None
    has_identical: bool | None = None
    min_size: int | None = None


class BulkExecute(BulkFilter):
    token: str
    confirm: int | None = None


def _plan(conn, body: BulkFilter) -> BulkPlan:
    if body.band is not None and body.band not in BANDS:
        raise HTTPException(status_code=400, detail=f"band must be one of {BANDS}")
    return plan_for_filter(
        conn,
        settings_db.all(conn),
        band=body.band,
        folder_id=parse_id(body.folder_id) if body.folder_id else None,
        has_identical=body.has_identical,
        min_size=body.min_size,
    )


def _summary(plan: BulkPlan, threshold: int) -> dict:
    return {
        "matched": plan.matched,
        "planned": len(plan.groups),
        "skipped": plan.skipped,
        "files_to_delete": plan.files_to_delete,
        "folders": list(plan.folders),
        "token": plan.token,
        "threshold": threshold,
        "typed_confirmation_required": plan.files_to_delete > threshold,
    }


@router.post("/preview")
async def preview(body: BulkFilter, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    return _summary(_plan(conn, body), int(settings_db.get(conn, "bulk.confirm_threshold")))


@router.post("/execute", status_code=201)
async def execute(body: BulkExecute, request: Request, name: str = Depends(get_profile)) -> dict:
    conn = get_state(request).registry.conn(name)
    plan = _plan(conn, body)
    if not plan.groups:
        raise HTTPException(
            status_code=400,
            detail="No group matches the filter with a complete default resolution; nothing to do.",
        )
    if plan.token != body.token:
        raise HTTPException(
            status_code=409, detail="The queue changed since the preview; preview again."
        )
    threshold = int(settings_db.get(conn, "bulk.confirm_threshold"))
    if plan.files_to_delete > threshold and body.confirm != plan.files_to_delete:
        raise HTTPException(
            status_code=400,
            detail=f"This run deletes {plan.files_to_delete} files; type that number to confirm.",
        )
    job_id = jobs_db.enqueue(
        conn,
        type="bulk_resolve",
        kind="io",
        priority=PRIORITY_USER,
        params={
            "groups": [
                {
                    "group_id": id_to_str(g.group_id),
                    "signature": g.signature,
                    "delete": [id_to_str(f) for f in g.delete],
                }
                for g in plan.groups
            ],
            "files_to_delete": plan.files_to_delete,
        },
    )
    return jobs_db.to_dict(jobs_db.get(conn, job_id))
```

`app.py`: import `bulk as bulk_api` and `app.include_router(bulk_api.router)` **before** `groups_api.router` (so `/groups/bulk/preview` is not captured by `/groups/{group_id}`; FastAPI matches in registration order).

- [ ] **Step 5: Run the tests, lint, commit**

Run: `uv run pytest tests/integration/test_bulk_resolve.py tests/integration/test_api_groups.py tests/integration/test_actions_job.py tests/unit/test_domain_bulk.py tests/unit/test_settings_db.py -q && uv run ruff check . && uv run ruff format .` — Expected: pass. (If `test_settings_db` enumerates defaults, add the new key there.)

```bash
git add visualstore/domain/bulk.py visualstore/jobs/resolve.py visualstore/jobs/bulkplan.py visualstore/jobs/io/bulk_resolve.py visualstore/api/bulk.py visualstore/db/settings.py visualstore/db/pairs.py visualstore/api/groups.py visualstore/jobs/io/actions.py visualstore/jobs/io/__init__.py visualstore/jobs/types.py visualstore/app.py tests/unit/test_domain_bulk.py tests/integration/test_bulk_resolve.py
git commit -m "feat: bulk default-rule resolution with preview, token and typed confirmation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Bulk UI on the review queue

**Files:**
- Modify: `web/src/pages/Groups.tsx`, `web/src/types.ts`, `web/e2e/start-server.sh`, `web/e2e/smoke.spec.ts`

**Interfaces:**
- `BulkPreview { matched; planned; skipped; files_to_delete; folders: string[]; token; threshold; typed_confirmation_required }`.
- Panel "Default rules for this filter": **Preview** posts the current filter (`band`, `folder_id`, `has_identical`, `min_size`; status is always open) to `/groups/bulk/preview`; the result sentence reads `N groups match, M get the default resolution (S skipped), F files will be moved to the trash from Folders: …`; when typed confirmation is required an input labelled `Type F to confirm` appears; **Execute** posts `/groups/bulk/execute` and shows `Bulk resolution queued as job …`.

- [ ] **Step 1: Implement**

`types.ts`:

```ts
export interface BulkPreview {
  matched: number;
  planned: number;
  skipped: number;
  files_to_delete: number;
  folders: string[];
  token: string;
  threshold: number;
  typed_confirmation_required: boolean;
}
```

`Groups.tsx`: import `post`, `type BulkPreview`, `Link`; add state `const [bulk, setBulk] = useState<BulkPreview | null>(null); const [typed, setTyped] = useState(""); const [notice, setNotice] = useState<string | null>(null);`; a `filterBody` memo:

```tsx
  const filterBody = {
    band: bandFilter || null,
    folder_id: folderId || null,
    has_identical: identical ? true : null,
    min_size: minSize,
  };

  async function previewBulk() {
    setError(null);
    setNotice(null);
    try {
      setBulk(await post<BulkPreview>(`/api/p/${name}/groups/bulk/preview`, filterBody));
      setTyped("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not preview.");
    }
  }

  async function executeBulk() {
    if (!bulk) return;
    setError(null);
    try {
      const job = await post<{ id: string }>(`/api/p/${name}/groups/bulk/execute`, {
        ...filterBody,
        token: bulk.token,
        confirm: bulk.typed_confirmation_required ? Number(typed) : null,
      });
      setNotice(`Bulk resolution queued as job ${job.id}.`);
      setBulk(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not execute.");
    }
  }
```

Reset `bulk` whenever a filter changes: add `setBulk(null)` in each filter `onChange` (status, folder, band, identical, min size). Render under the filter form:

```tsx
      <details className="bulk" open={bulk !== null}>
        <summary>Default rules for this filter (open groups only)</summary>
        <p className="muted">
          Applies the default resolution (design rules: keep archive, delete inbox; inbox only keeps the first) to every open group matching the filter. Groups the rules cannot decide are skipped.
        </p>
        <div className="actions-row">
          <button type="button" onClick={() => void previewBulk()}>
            Preview
          </button>
          {bulk && (
            <>
              <span role="status">
                {bulk.matched} groups match, {bulk.planned} get the default resolution ({bulk.skipped} skipped), {bulk.files_to_delete} files will be moved to the trash
                {bulk.folders.length > 0 ? ` from ${bulk.folders.join(", ")}` : ""}.
              </span>
              {bulk.typed_confirmation_required && (
                <label className="inline-label">
                  Type {bulk.files_to_delete} to confirm
                  <input value={typed} onChange={(e) => setTyped(e.target.value)} style={{ width: "6rem" }} />
                </label>
              )}
              <button
                type="button"
                className="danger"
                disabled={bulk.planned === 0 || (bulk.typed_confirmation_required && Number(typed) !== bulk.files_to_delete)}
                onClick={() => void executeBulk()}
              >
                Execute
              </button>
            </>
          )}
        </div>
        {notice && (
          <p className="muted">
            {notice} <Link to={`/p/${name}/jobs`}>Jobs</Link>
          </p>
        )}
      </details>
```

Note: `status` filter defaults to `open` on this page already; the bulk panel ignores a different status choice (the sentence says "open groups only").

- [ ] **Step 2: Extend the e2e**

`web/e2e/start-server.sh`: after `c.png`, add a second byte-identical pair in another directory: `mkdir -p "$tmp/media/lib/sub2"` and in the Python block `(tmp / "media/lib/sub2/d.png").write_bytes(png((0, 0, 255)))` and `(tmp / "media/lib/sub2/e.png").write_bytes(png((0, 0, 255)))` (blue: far from red and green under the tiny model, so two separate identical groups form).

`web/e2e/smoke.spec.ts`, in the second test: the queue now holds two groups of 2 files, so use `.first()` for the "2 files" link (`page.getByRole("link", { name: "2 files" }).first()`), keep the keyboard resolution of the first group, and after `await expect(page).toHaveURL(/\/p\/demo\/groups$/);` add:

```ts
  await expect(page.getByRole("link", { name: "2 files" })).toHaveCount(1, { timeout: 20_000 });
  await page.getByText("Default rules for this filter").click();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("status")).toContainText("1 groups match, 1 get the default resolution (0 skipped), 1 files will be moved to the trash");
  await page.getByRole("button", { name: "Execute" }).click();
  await expect(page.getByText(/Bulk resolution queued as job/)).toBeVisible();
  await expect(page.getByRole("link", { name: "2 files" })).toHaveCount(0, { timeout: 20_000 });
```

Then the trash part: replace the single `Restore` click with selecting all: after `await expect(page.locator("code", { hasText: "b.png" })).toBeVisible(...)` add `await expect(page.locator("code", { hasText: "e.png" })).toBeVisible();`, then `await page.getByRole("button", { name: "Select all" }).click(); await page.getByRole("button", { name: /Restore selected/ }).click();` and keep the final "Nothing in the trash." expectation.

- [ ] **Step 3: Run the checks and the e2e, commit**

Run: `cd web && npm run lint && npm test -- --run && npm run build && npm run e2e` — Expected: pass.

```bash
git add web/src/pages/Groups.tsx web/src/types.ts web/e2e/start-server.sh web/e2e/smoke.spec.ts
git commit -m "feat(web): bulk rule panel with preview, typed confirmation and execution

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Move and rename actions

**Files:**
- Modify: `visualstore/jobs/io/actions.py`, `visualstore/db/files.py`, `visualstore/api/files.py`, `tests/integration/test_actions_job.py`, `tests/integration/test_api_groups.py`, `web/src/pages/FileDetail.tsx`, `web/src/types.ts`

**Interfaces:**
- `files_db.update_location(conn, file_id, folder_id, directory, name) -> None`.
- `move_file(src, dest, *, allow_suffix=True)` — with `allow_suffix=False` an existing `dest` raises `ActionError("target exists")` before anything is touched.
- `apply_action` params gain `"move": [{"file_id", "folder_id", "directory"}]` and `"rename": [{"file_id", "name"}]`; result gains `"moved"`, `"renamed"`; skip reasons: `missing row`, `missing file`, `no such folder`, `bad name`, `exists`.
- `POST /api/p/{name}/files/{id}/move {folder_id, directory}` and `POST /api/p/{name}/files/{id}/rename {name}` → 201 `apply_action` job at `PRIORITY_USER`; 400 for a bad directory/name; 404 for an unknown Folder; 409 when the target exists on disk or in the table at request time.
- `FileDetail` page: a **Rename** form (name input) and a **Move** form (Folder select + directory input), both posting and then showing `… queued as job …`.

- [ ] **Step 1: Write the failing job tests**

Append to `tests/integration/test_actions_job.py`:

```python
def test_rename_and_move_keep_the_row_id_hash_and_pairs(world):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a, b = add("sub/a.jpg", b"aaa"), add("sub/b.jpg", b"bbb")
    pairs.upsert_vector_pair(conn, a, b, 0.2, "m")
    result = run_apply_action(ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "renamed.jpg"}]}))
    assert result["renamed"] == 1 and result["skipped"] == []
    row = files.get_file(conn, a)
    assert row["directory"] == "sub" and row["name"] == "renamed.jpg" and row["sha1"] == hashlib.sha1(b"aaa").digest()
    assert (lib / "sub" / "renamed.jpg").exists() and not (lib / "sub" / "a.jpg").exists()
    assert len(pairs.pairs_of_file(conn, a)) == 1
    assert any(r["type"] == "rename" for r in conn.execute("SELECT type FROM action"))

    other_root = lib.parent / "arch"
    other_root.mkdir()
    arch = folders.create_folder(conn, name="Arch", path=str(other_root), role="archive", compare_scope="all")
    result = run_apply_action(ctx_for(reg, {"move": [{"file_id": id_to_str(a), "folder_id": id_to_str(arch), "directory": "x/y"}]}))
    assert result["moved"] == 1 and result["skipped"] == []
    row = files.get_file(conn, a)
    assert row["folder_id"] == arch and row["directory"] == "x/y" and row["name"] == "renamed.jpg"
    assert (other_root / "x" / "y" / "renamed.jpg").read_bytes() == b"aaa"
    assert not (lib / "sub").exists()  # empty source directory removed
    assert set(groups.pop_dirty(conn)) == {a}  # the role may have changed: regroup
    assert len(pairs.pairs_of_file(conn, a)) == 1


def test_rename_and_move_never_overwrite(world):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a, b = add("a.jpg", b"aaa"), add("b.jpg", b"bbb")
    result = run_apply_action(ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "b.jpg"}]}))
    assert result["renamed"] == 0 and result["skipped"] == [{"id": id_to_str(a), "reason": "exists"}]
    assert (lib / "a.jpg").read_bytes() == b"aaa" and (lib / "b.jpg").read_bytes() == b"bbb"
    (lib / "sub" / "a.jpg").write_bytes(b"on disk only")
    result = run_apply_action(ctx_for(reg, {"move": [{"file_id": id_to_str(a), "folder_id": id_to_str(folder), "directory": "sub"}]}))
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "exists"}]
    assert (lib / "a.jpg").read_bytes() == b"aaa"
    result = run_apply_action(ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "../x.jpg"}]}))
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "bad name"}]
    result = run_apply_action(ctx_for(reg, {"move": [{"file_id": id_to_str(a), "folder_id": id_to_str(b"\x00" * 16), "directory": ""}]}))
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "no such folder"}]
```

Run: `uv run pytest tests/integration/test_actions_job.py -q -k "rename or move"` — Expected: FAIL (`KeyError: 'renamed'`).

- [ ] **Step 2: Implement the job side**

`visualstore/db/files.py`:

```python
def update_location(
    conn: sqlite3.Connection, file_id: bytes, folder_id: bytes, directory: str, name: str
) -> None:
    with conn:
        conn.execute(
            "UPDATE file SET folder_id = ?, directory = ?, name = ? WHERE id = ?",
            (folder_id, directory, name, file_id),
        )
```

In `visualstore/jobs/io/actions.py`:

1. `move_file(src: Path, dest: Path, *, allow_suffix: bool = True) -> Path`: at the top, `if allow_suffix: dest = _unique(dest)` / `elif dest.exists(): raise ActionError("target exists")`.
2. Add the relocation primitive and the items (imports: `from visualstore.domain.paths import TRASH_DIR, normalise_relative`):

```python
def _relocate(
    conn: sqlite3.Connection, file_id: bytes, target_folder, directory: str, name: str, kind: str
) -> str | None:
    """Move/rename one file: disk first, then the row; never overwrites (design §8.5)."""
    row = files_db.get_file(conn, file_id)
    if row is None:
        return "missing row"
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return "bad name"
    try:
        directory = normalise_relative(directory)
    except ValueError:
        return "bad name"
    source_folder = folders_db.get_folder(conn, bytes(row["folder_id"]))
    src = Path(source_folder["path"]) / files_db.relative_path(row["directory"], row["name"])
    if not src.is_file():
        return "missing file"
    dest = Path(target_folder["path"]) / files_db.relative_path(directory, name)
    if dest == src:
        return None
    occupied = files_db.find_file(conn, bytes(target_folder["id"]), directory, name)
    if dest.exists() or (occupied is not None and bytes(occupied["id"]) != file_id):
        return "exists"
    move_file(src, dest, allow_suffix=False)
    before = {"folder_id": id_to_str(bytes(row["folder_id"])), "directory": row["directory"], "name": row["name"], "path": str(src)}
    after = {"folder_id": id_to_str(bytes(target_folder["id"])), "directory": directory, "name": name, "path": str(dest)}
    files_db.update_location(conn, file_id, bytes(target_folder["id"]), directory, name)
    actions_db.record(conn, kind, file_id, before, after)
    if bytes(target_folder["id"]) != bytes(row["folder_id"]):
        groups_db.mark_dirty(conn, [file_id])  # the role may differ: preselection changes
    remove_empty_parents(src.parent, Path(source_folder["path"]))
    return None
```

3. In `execute_action`, add `"moved": 0, "renamed": 0` to `result` and after the restore loop:

```python
    for item in params.get("move", []):
        file_id = id_from_str(item["file_id"])
        target = folders_db.get_folder(conn, id_from_str(item["folder_id"]))
        reason = "no such folder" if target is None else _relocate(conn, file_id, target, item.get("directory", ""), files_db.get_file(conn, file_id)["name"] if files_db.get_file(conn, file_id) else "", "move")
        if reason:
            result["skipped"].append({"id": item["file_id"], "reason": reason})
        else:
            result["moved"] += 1
    for item in params.get("rename", []):
        file_id = id_from_str(item["file_id"])
        row = files_db.get_file(conn, file_id)
        if row is None:
            result["skipped"].append({"id": item["file_id"], "reason": "missing row"})
            continue
        target = folders_db.get_folder(conn, bytes(row["folder_id"]))
        reason = _relocate(conn, file_id, target, row["directory"], item["name"], "rename")
        if reason:
            result["skipped"].append({"id": item["file_id"], "reason": reason})
        else:
            result["renamed"] += 1
    if (result["moved"] or result["renamed"]) and not jobs_db.has_active(conn, "regroup"):
        jobs_db.enqueue(conn, type="regroup", kind="io", priority=PRIORITY_INBOX)
```

(Write the `move` loop with an explicit `row = files_db.get_file(conn, file_id)` / `"missing row"` check instead of the inline conditional so it reads like the rename loop.)

- [ ] **Step 3: Run the job tests, then write the failing API test**

Run: `uv run pytest tests/integration/test_actions_job.py -q` — Expected: pass.

Append to `tests/integration/test_api_groups.py`:

```python
def test_move_and_rename_endpoints_validate_and_queue(client, seeded):
    s = seeded
    b = id_to_str(s["b"])
    assert client.post(f"/api/p/a/files/{b}/rename", json={"name": "../x.jpg"}).status_code == 400
    assert client.post(f"/api/p/a/files/{b}/rename", json={"name": "c.jpg"}).status_code == 409  # in the table
    assert client.post(f"/api/p/a/files/{b}/move", json={"folder_id": id_to_str(s["arch"]), "directory": "../up"}).status_code == 400
    assert client.post(f"/api/p/a/files/{b}/move", json={"folder_id": "00000000-0000-4000-8000-000000000000", "directory": ""}).status_code == 404
    assert client.post(f"/api/p/a/files/{b}/move", json={"folder_id": id_to_str(s["arch"]), "directory": ""}).status_code == 409  # a.jpg... no: b.jpg is free there
```

Correct the last line: moving `b.jpg` into Arch root is free (Arch holds `a.jpg`), so assert `201` for it and check the queued job:

```python
    job = client.post(f"/api/p/a/files/{b}/move", json={"folder_id": id_to_str(s["arch"]), "directory": ""})
    assert job.status_code == 201 and job.json()["params"]["move"] == [{"file_id": b, "folder_id": id_to_str(s["arch"]), "directory": ""}]
    done = wait_for(client, "a", job.json()["id"])
    assert done["state"] == "done" and done["progress"]["result"]["moved"] == 1
    assert client.get(f"/api/p/a/files/{b}").json()["folder_name"] == "Arch"
    renamed = client.post(f"/api/p/a/files/{b}/rename", json={"name": "b2.jpg"})
    assert renamed.status_code == 201
    assert wait_for(client, "a", renamed.json()["id"])["progress"]["result"]["renamed"] == 1
```

Run: `uv run pytest tests/integration/test_api_groups.py -q -k move_and_rename` — Expected: FAIL (404/405).

- [ ] **Step 4: Implement the endpoints and the UI**

`visualstore/api/files.py` (imports: `from pydantic import BaseModel`, `from visualstore.db import jobs as jobs_db`, `from visualstore.domain.paths import normalise_relative`, `from visualstore.jobs.types import PRIORITY_USER`):

```python
class Move(BaseModel):
    folder_id: str
    directory: str = ""


class Rename(BaseModel):
    name: str


def _check_name(name: str) -> None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="The name must be a single path component.")


def _check_free(conn, folder, directory: str, name: str, file_id: bytes) -> None:
    occupied = files_db.find_file(conn, bytes(folder["id"]), directory, name)
    target = Path(folder["path"]) / files_db.relative_path(directory, name)
    if (occupied is not None and bytes(occupied["id"]) != file_id) or target.exists():
        raise HTTPException(status_code=409, detail="A file with that path already exists.")


def _queue(conn, params: dict) -> dict:
    job_id = jobs_db.enqueue(conn, type="apply_action", kind="io", priority=PRIORITY_USER, params=params)
    return jobs_db.to_dict(jobs_db.get(conn, job_id))


@router.post("/{file_id}/move", status_code=201)
async def move_file_endpoint(
    file_id: str, body: Move, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, _ = _file_and_folder(conn, fid)
    try:
        directory = normalise_relative(body.directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    target = folders_db.get_folder(conn, parse_id(body.folder_id))
    if target is None:
        raise HTTPException(status_code=404, detail="No such Folder.")
    _check_free(conn, target, directory, row["name"], fid)
    return _queue(conn, {"move": [{"file_id": file_id, "folder_id": body.folder_id, "directory": directory}]})


@router.post("/{file_id}/rename", status_code=201)
async def rename_file_endpoint(
    file_id: str, body: Rename, request: Request, name: str = Depends(get_profile)
) -> dict:
    conn = get_state(request).registry.conn(name)
    fid = parse_id(file_id)
    row, folder = _file_and_folder(conn, fid)
    _check_name(body.name)
    _check_free(conn, folder, row["directory"], body.name, fid)
    return _queue(conn, {"rename": [{"file_id": file_id, "name": body.name}]})
```

`FileDetail.tsx`: load `Folder[]` too (`get<Folder[]>(`/api/p/${name}/folders`)`), add state `newName`, `moveTo: {folder_id, directory}`, and two inline forms after "Open original":

```tsx
      <h2>Rename / move</h2>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); void act(`/api/p/${name}/files/${detail.id}/rename`, { name: newName }, "Rename"); }}>
        <label>
          New name
          <input value={newName} onChange={(e) => setNewName(e.target.value)} required />
        </label>
        <button type="submit">Rename</button>
      </form>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); void act(`/api/p/${name}/files/${detail.id}/move`, moveTo, "Move"); }}>
        <label>
          Folder
          <select value={moveTo.folder_id} onChange={(e) => setMoveTo({ ...moveTo, folder_id: e.target.value })} required>
            <option value="">choose</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Directory (relative, empty for the root)
          <input value={moveTo.directory} onChange={(e) => setMoveTo({ ...moveTo, directory: e.target.value })} />
        </label>
        <button type="submit" disabled={!moveTo.folder_id}>
          Move
        </button>
      </form>
```

with

```tsx
  async function act(path: string, body: unknown, what: string) {
    setError(null);
    try {
      const job = await post<Job>(path, body);
      setNotice(`${what} queued as job ${job.id}. The page updates once it has run.`);
      window.setTimeout(() => void load(), 2000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${what.toLowerCase()}.`);
    }
  }
```

(`newName` initialised to `detail.name` once loaded; `moveTo` to `{ folder_id: detail.folder_id, directory: detail.directory }`.)

- [ ] **Step 5: Run everything, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format . && (cd web && npm run lint && npm test -- --run && npm run build)` — Expected: pass.

```bash
git add visualstore/jobs/io/actions.py visualstore/db/files.py visualstore/api/files.py tests/integration/test_actions_job.py tests/integration/test_api_groups.py web/src/pages/FileDetail.tsx web/src/types.ts
git commit -m "feat: move and rename actions with no-overwrite semantics; file detail forms

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Documentation and status

**Files:**
- Modify: `README.md`, `docs/plans/README.md`, `CLAUDE.md`, `docs/design/06-review-ui.md` (only if a divergence is found), `docs/decisions/0006-m4-implementation-choices.md` (facts learned)

- [ ] **Step 1: README**

Under "What it does" (or a new "Reviewing" paragraph in "Running"), describe the review workflow in five sentences: the queue filters by status, band and Folder; group detail compares the selected member with its nearest neighbour side by side, as an overlay, or blinking, with the keys listed in the Global Constraints; `Enter` confirms and moves on; the file detail page lists candidate pairs beyond the grouping threshold with "related" / "unrelated" judgments and move / rename; "Default rules for this filter" on the queue previews and executes the default resolution for every open group matching the filter, asking for the delete count to be typed when it exceeds `bulk.confirm_threshold`.

- [ ] **Step 2: Cross-check design §8.3 and record**

Read `docs/design/06-review-ui.md` §8.3 against the implementation. Known divergences to record in ADR-0006 under "Facts learned while shipping": video frame strips are milestone 5; "resolution" (pixel dimensions) is not stored on `file` yet (milestone 5 adds media metadata) so the member list shows size and distance but not dimensions. Anything else found: record there, and if the design text is wrong, correct the design chapter in the same commit.

- [ ] **Step 3: Status**

`docs/plans/README.md`: milestone 4 → `shipped`. `CLAUDE.md` status line: milestones 1 to 4 shipped; milestone 5 (video and animated images) is next and its task-level plan must be expanded first.

- [ ] **Step 4: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && (cd web && npm run lint && npm test -- --run && npm run build && npm run e2e)` and the Docker media suite: `docker run --rm -v "$PWD/visualstore:/app/visualstore:ro" -v "$PWD/tests:/app/tests:ro" visualstore:test python -m pytest -p no:cacheprovider tests/unit/test_decode.py tests/integration/test_api_preview.py tests/unit/test_fingerprint_vectors.py tests/unit/test_match.py tests/unit/test_vector_index.py -q` — Expected: all green.

```bash
git add README.md docs/plans/README.md CLAUDE.md docs/decisions/0006-m4-implementation-choices.md docs/design/06-review-ui.md
git commit -m "docs: review workflow, milestone 4 status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

Spec coverage ([11 §13 item 4](../design/11-roadmap.md#13-milestones)): image comparison and keyboard shortcuts in group detail (Tasks 1, 3, 4); review queue filters (already shipped in milestone 3: band, Folder, status, identical, min size) and bulk rules (Tasks 6, 7); candidate pairs on the file detail page (Task 5). §8.5 move / rename (Task 8). §8.3 "resolution" (dimensions) and video frame strip are deferred to milestone 5 and recorded in Task 9.

Review Focus → tests: 1 and 2 in `test_bulk_resolve.py`; 3 in `test_actions_job.py`; 4 in `test_api_preview.py` (server side) and the `Compare` fallback plus e2e (client side); 5 in `test_api_groups.py::test_file_detail_lists_candidates_beyond_the_grouping_threshold`.

Names checked across tasks: `nearest`, `neighbours`; `preview_webp`, `ProfilePaths.previews`; `CompareMode`, `KeyState.mode/target`, `applyKey(state, key, nearest)`, `compareTarget`, `layers`; `signature`, `GroupPlan`, `BulkPlan`, `plan_group`, `build_plan`, `plan_for_group`, `plan_for_filter`; `commit_decisions`; `execute_action`; `inner_pairs`; `update_location`; `move_file(allow_suffix)`; `_relocate`; job type `bulk_resolve` in `IO_HANDLERS` / `KIND_OF`; routes `/groups/bulk/preview`, `/groups/bulk/execute`, `/files/{id}/preview`, `/files/{id}/move`, `/files/{id}/rename`; setting `bulk.confirm_threshold`.
