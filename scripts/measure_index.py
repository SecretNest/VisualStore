"""Measure index size and recall for the quantisation / PCA options on a profile's own
vectors (ADR-0008). Read-only on the database.

    uv run python scripts/measure_index.py --vectors /data/profiles/<name>/vectors.db \
        --model sscd-resnet50-512-v1 --sample 20000 --k 10 --pca 256
"""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
import time
from pathlib import Path

import numpy as np

from visualstore.index import pca
from visualstore.index.usearch_index import VectorIndex


def load_vectors(db: Path, model_id: str, sample: int, seed: int = 0) -> np.ndarray:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT vector FROM fingerprint WHERE model_id = ?", (model_id,)).fetchall()
    conn.close()
    if not rows:
        raise SystemExit(f"no vectors for {model_id} in {db}")
    vectors = np.array([np.frombuffer(r[0], dtype=np.float16) for r in rows]).astype(np.float32)
    if len(vectors) > sample:
        picked = np.random.default_rng(seed).choice(len(vectors), size=sample, replace=False)
        vectors = vectors[np.sort(picked)]
    return vectors


def exact_neighbours(vectors: np.ndarray, queries: np.ndarray, k: int) -> np.ndarray:
    scores = queries @ vectors.T
    return np.argsort(-scores, axis=1)[:, 1 : k + 1]  # skip self


def measure(vectors: np.ndarray, k: int, pca_dims: int | None, queries: int = 200) -> list[dict]:
    rng = np.random.default_rng(1)
    q_idx = rng.choice(len(vectors), size=min(queries, len(vectors)), replace=False)
    truth = exact_neighbours(vectors, vectors[q_idx], k)
    variants = [("f16", None), ("i8", None)]
    if pca_dims:
        variants += [("f16", pca_dims), ("i8", pca_dims)]
    out = []
    for quantization, dims in variants:
        data = vectors
        if dims:
            data = pca.project(pca.fit(vectors, dims), vectors)
        started = time.perf_counter()
        index = VectorIndex.empty(data.shape[1], quantization)
        index.add(list(range(len(data))), data)
        build_s = time.perf_counter() - started
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.usearch"
            index.save(path)
            size = path.stat().st_size
        hits = 0
        for row, qi in enumerate(q_idx):
            found = {key for key, _ in index.search(data[qi], k + 1) if key != qi}
            hits += len(found & set(truth[row].tolist()))
        out.append(
            {
                "variant": f"{quantization}" + (f"+pca{dims}" if dims else ""),
                "bytes": size,
                "build_s": round(build_s, 2),
                f"recall@{k}": round(hits / (len(q_idx) * k), 4),
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--sample", type=int, default=20_000)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--pca", type=int, default=0)
    args = parser.parse_args()
    vectors = load_vectors(args.vectors, args.model, args.sample)
    rows = measure(vectors, args.k, args.pca or None)
    keys = list(rows[0])
    print(" | ".join(keys))
    for r in rows:
        print(" | ".join(str(r[key]) for key in keys))


if __name__ == "__main__":
    main()
