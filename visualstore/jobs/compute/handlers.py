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


def _embed_image(path: str, model: dict, max_pixels: int) -> tuple[list[dict], str]:
    from visualstore.media.decode import load_image, thumbnail_webp, to_model_input

    decoded = load_image(path, max_pixels)
    session = _session(model)
    tensor = to_model_input(decoded, int(model["input_size"]), model["mean"], model["std"])
    vector = session.embed(tensor)[0]
    encoded = base64.b64encode(vector.astype(np.float16).tobytes()).decode("ascii")
    frames = [{"index": 0, "time_ms": 0, "vector": encoded}]
    thumb = base64.b64encode(thumbnail_webp(decoded, 256)).decode("ascii")
    return frames, thumb


def fingerprint(params: dict, report: Report) -> dict:
    """SHA-1 for every file; vectors and a thumbnail for still images (design §7.2)."""
    from visualstore.media.errors import NotMedia, Unreadable

    entries = params.get("files", [])
    model = params.get("model")
    max_pixels = int(params.get("max_pixels", 80_000_000))
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
            if model and entry.get("media_type") == "image":
                try:
                    frames, thumb = _embed_image(path, model, max_pixels)
                    record.update({"frames": frames, "thumbnail": thumb, "model_id": model["id"]})
                except NotMedia as exc:
                    record.update({"state": "not_media", "reason": str(exc)[:200]})
                except Unreadable as exc:
                    record.update({"state": "unreadable", "reason": str(exc)[:200]})
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
    from visualstore.index.usearch_index import VectorIndex

    model = params["model"]
    dim = int(model["dim"])
    index_path = Path(params["index_path"])
    index = VectorIndex.open_or_create(index_path, dim)
    removed = index.remove([int(s) for s in params.get("remove", [])])
    entries = params.get("entries", [])
    keys = [int(e["seq"]) for e in entries]
    decoded = [
        np.frombuffer(base64.b64decode(e["vector"]), dtype=np.float16).astype(np.float32)
        for e in entries
    ]
    vectors = np.array(decoded, dtype=np.float32).reshape(len(entries), dim)
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


HANDLERS: dict[str, Handler] = {
    "ping": ping,
    "fail": fail,
    "fingerprint": fingerprint,
    "match": match,
}
