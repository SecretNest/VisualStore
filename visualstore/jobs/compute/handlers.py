"""Compute job handlers, run inside child processes.

A handler receives the job params and a report callback for progress and returns a
JSON-serialisable result dict. Milestone 3 adds fingerprint, match, rebuild_index, eval.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

Report = Callable[[dict], None]
Handler = Callable[[dict, Report], dict]


def ping(params: dict, report: Report) -> dict:
    count = int(params.get("count", 1))
    sleep_ms = int(params.get("sleep_ms", 0))  # lets tests simulate long-running compute
    for i in range(count):
        if sleep_ms:
            time.sleep(sleep_ms / 1000)
        report({"done": i + 1, "total": count, "message": "ping"})
    return {"pong": params.get("echo"), "pid": os.getpid()}


def fail(params: dict, report: Report) -> dict:
    raise RuntimeError(str(params.get("message", "requested failure")))


_CHUNK = 1024 * 1024


def sha1_of(path: str) -> bytes:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.digest()


_SESSIONS: dict[str, object] = {}


def _session(model: dict):
    from visualstore.models.infer import Session
    from visualstore.models.registry import ModelError, ModelSpec

    path = model["path"]
    if path not in _SESSIONS:
        if not os.path.isfile(path):
            raise ModelError(
                f"model file missing: {path} (download it or set VISUALSTORE_MODEL_DIR)"
            )
        spec = ModelSpec(
            id=model["id"],
            url="",
            sha256="",
            dim=int(model["dim"]),
            input_size=int(model["input_size"]),
            mean=tuple(model["mean"]),
            std=tuple(model["std"]),
            description="",
        )
        _SESSIONS[path] = Session(path, spec)
    return _SESSIONS[path]


def _embed_frames(frames: list, model: dict, thumb_index: int) -> tuple[list[dict], str]:
    from visualstore.media.decode import Decoded, thumbnail_webp, to_model_input

    session = _session(model)
    size = int(model["input_size"])
    batch = np.concatenate(
        [
            to_model_input(
                Decoded(f.image, f.image.width, f.image.height), size, model["mean"], model["std"]
            )
            for f in frames
        ]
    )
    vectors = session.embed(batch)
    out = [
        {
            "index": f.index,
            "time_ms": f.time_ms,
            "vector": base64.b64encode(v.astype(np.float16).tobytes()).decode("ascii"),
        }
        for f, v in zip(frames, vectors, strict=True)
    ]
    t = frames[thumb_index]
    thumb = thumbnail_webp(Decoded(t.image, t.image.width, t.image.height), 256)
    return out, base64.b64encode(thumb).decode("ascii")


def _frames_for(path: str, media_type: str, max_pixels: int, sampling: dict) -> tuple[list, int]:
    """The sampled frames of a file and which one becomes the thumbnail."""
    from visualstore.media import animated, video
    from visualstore.media.decode import load_image
    from visualstore.media.frames import Frame, sample_times

    max_frames = int(sampling.get("max_per_file", 8))
    timeout = float(sampling.get("ffmpeg_timeout_s", 120))
    if media_type == "video":
        info = video.probe(path, timeout)
        times = sample_times(info.duration_s, max_frames, info.first_keyframe_s)
        dims = (info.width, info.height)
        frames = video.frames_at_times(path, times, timeout, max_pixels, dims)
        return frames, len(frames) // 2
    if media_type == "animated":
        return animated.frames(path, max_frames, timeout, max_pixels), 0
    decoded = load_image(path, max_pixels)
    if decoded.pages > 1:  # an animated webp with an image extension (design §5.1)
        return animated.frames(path, max_frames, timeout, max_pixels), 0
    return [Frame(0, 0, decoded.image)], 0


def fingerprint(params: dict, report: Report) -> dict:
    """SHA-1 for every file; vectors and a thumbnail for images, animated images and video
    frames (design §7.2, §7.3)."""
    from visualstore.media.errors import NotMedia, Unreadable
    from visualstore.models.registry import ModelError

    entries = params.get("files", [])
    model = params.get("model")
    max_pixels = int(params.get("max_pixels", 80_000_000))
    sampling = params.get("frames", {})
    out = []
    for i, entry in enumerate(entries, start=1):
        path = entry["path"]
        try:
            st = os.stat(path)
            record = {
                "id": entry["id"],
                "sha1": sha1_of(path).hex(),
                "state": "fingerprinted",
                "size": st.st_size,
                "mtime": st.st_mtime_ns,
            }
            if model and entry.get("media_type") in ("image", "animated", "video"):
                try:
                    frames, thumb_index = _frames_for(
                        path, entry["media_type"], max_pixels, sampling
                    )
                    frames_out, thumb = _embed_frames(frames, model, thumb_index)
                    record.update(
                        {"frames": frames_out, "thumbnail": thumb, "model_id": model["id"]}
                    )
                except NotMedia as exc:
                    record.update({"state": "not_media", "reason": str(exc)[:200]})
                except Unreadable as exc:
                    record.update({"state": "unreadable", "reason": str(exc)[:200]})
                except ModelError:
                    raise  # the whole batch is pointless without the model; fail visibly
                except Exception as exc:  # noqa: BLE001 - one odd file must not cost the batch
                    reason = f"{type(exc).__name__}: {exc}"[:200]
                    record.update({"state": "unreadable", "reason": reason})
            out.append(record)
        except FileNotFoundError:
            out.append({"id": entry["id"], "sha1": None, "state": "missing", "size": 0})
        except OSError:
            out.append({"id": entry["id"], "sha1": None, "state": "unreadable", "size": 0})
        if i % 8 == 0 or i == len(entries):
            report({"done": i, "total": len(entries), "message": os.path.basename(path)})
    return {"files": out}


def match(params: dict, report: Report) -> dict:
    """Add a batch of vectors to the profile index and return raw neighbour hits (design §7.4).
    The sink applies compare scope and writes pairs; this function never sees the database."""
    from visualstore.index.usearch_index import VectorIndex, inspect

    model = params["model"]
    dim = int(model["dim"])
    options = _index_options(params, dim)
    index_path = Path(params["index_path"])
    on_disk = inspect(index_path)
    if on_disk is not None and (
        on_disk["dimensions"] != options["index_dim"] or on_disk["dtype"] != options["quantization"]
    ):
        # The index was rebuilt with other options after this job was queued; its vectors were
        # in that rebuild's snapshot (or are matched by the build sink), so there is nothing to do.
        return {
            "hits": [],
            "added": 0,
            "removed": 0,
            "count": on_disk["count"],
            "skipped": "index options changed; covered by the rebuild",
        }
    index = VectorIndex.open_or_create(index_path, options["index_dim"], options["quantization"])
    removed = index.remove([int(s) for s in params.get("remove", [])])
    entries = params.get("entries", [])
    keys = [int(e["seq"]) for e in entries]
    decoded = [
        np.frombuffer(base64.b64decode(e["vector"]), dtype=np.float16).astype(np.float32)
        for e in entries
    ]
    vectors = _projected(np.array(decoded, dtype=np.float32).reshape(len(entries), dim), options)
    index.add(keys, vectors)
    index.save(index_path)
    limit = float(params.get("candidate_distance", 0.5))
    top_k = int(params.get("top_k", 64))
    hits: list[list] = []
    for i, key in enumerate(keys):
        for hit_key, distance in index.search(vectors[i], top_k):
            if hit_key != key and distance <= limit:
                hits.append([key, hit_key, round(distance, 6)])
        if (i + 1) % 16 == 0 or i + 1 == len(keys):
            report({"done": i + 1, "total": len(keys), "message": f"{len(hits)} hits"})
    return {"hits": hits, "added": len(keys), "removed": removed, "count": index.count}


def _index_options(params: dict, model_dim: int) -> dict:
    options = params.get("index") or {}
    return {
        "quantization": str(options.get("quantization", "f16")),
        "pca_dims": int(options.get("pca_dims", 0)),
        "pca_path": options.get("pca_path"),
        "index_dim": int(options.get("index_dim", model_dim)),
    }


def _projected(vectors: np.ndarray, options: dict) -> np.ndarray:
    """Model vectors → index vectors: identity, or the profile's PCA projection (ADR-0008)."""
    if not options.get("pca_path") or vectors.shape[0] == 0:
        return vectors.reshape(vectors.shape[0], options["index_dim"])
    from visualstore.index import pca

    return pca.project(pca.load(options["pca_path"]), vectors)


def build_index(params: dict, report: Report) -> dict:
    """Build a fresh index from an exported (keys, vectors) file and replace the profile's."""
    from visualstore.index.usearch_index import VectorIndex

    try:
        data = np.load(params["export_path"])
        keys = data["keys"].astype(np.uint64)
        options = _index_options(params, int(params["dim"]))
        vectors = _projected(data["vectors"].astype(np.float32), options)
        index = VectorIndex.empty(options["index_dim"], options["quantization"])
        step = 4096
        for start in range(0, len(keys), step):
            index.add(keys[start : start + step].tolist(), vectors[start : start + step])
            report(
                {"done": min(start + step, len(keys)), "total": len(keys), "message": "building"}
            )
        index.save(Path(params["index_path"]))
    finally:
        Path(params["export_path"]).unlink(missing_ok=True)  # never left behind (audit minor)
    return {"count": index.count, "model_id": params["model_id"], "dim": int(params["dim"])}


HANDLERS: dict[str, Handler] = {
    "ping": ping,
    "fail": fail,
    "fingerprint": fingerprint,
    "match": match,
    "build_index": build_index,
}
