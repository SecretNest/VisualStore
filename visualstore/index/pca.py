"""Per-profile PCA for the vector index (design §9.5, ADR-0008). Stored vectors stay raw."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Projection:
    mean: np.ndarray  # (dim,)
    components: np.ndarray  # (dims, dim)

    @property
    def dims(self) -> int:
        return int(self.components.shape[0])


def fit(vectors: np.ndarray, dims: int, sample: int = 100_000, seed: int = 0) -> Projection:
    rows, dim = vectors.shape
    if dims < 1 or dims >= dim:
        raise ValueError(f"pca dims must be between 1 and {dim - 1}, got {dims}")
    if rows < dims:
        raise ValueError(f"need at least {dims} vectors to fit {dims} components, have {rows}")
    if rows > sample:
        picked = np.random.default_rng(seed).choice(rows, size=sample, replace=False)
        vectors = vectors[np.sort(picked)]
    data = vectors.astype(np.float32)
    mean = data.mean(axis=0)
    _, _, vt = np.linalg.svd(data - mean, full_matrices=False)
    return Projection(mean=mean, components=np.ascontiguousarray(vt[:dims]))


def project(p: Projection, vectors: np.ndarray) -> np.ndarray:
    out = (np.asarray(vectors, dtype=np.float32) - p.mean) @ p.components.T
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.ascontiguousarray(out / norms, dtype=np.float32)


def save(p: Projection, path: Path) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, mean=p.mean, components=p.components)
    tmp.replace(path)


def load(path: Path) -> Projection:
    data = np.load(path)
    return Projection(
        mean=data["mean"].astype(np.float32), components=data["components"].astype(np.float32)
    )
