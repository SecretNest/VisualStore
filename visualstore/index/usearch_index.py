"""Thin wrapper around usearch for one profile's frame index (design §7.4, ADR-0005)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

# onnxruntime must be loaded before usearch's extension module: on the Debian bookworm image
# (usearch 2.26 / onnxruntime 1.31) importing onnxruntime *after* usearch segfaults the process,
# and the compute children need both (fingerprint embeds, match searches).
import onnxruntime  # noqa: F401, I001
from usearch.index import Index


class IndexError_(Exception):
    pass


def inspect(path: Path) -> dict | None:
    if not Path(path).is_file():
        return None
    try:
        meta = Index.metadata(str(path))
    except Exception as exc:  # noqa: BLE001 - usearch raises plain Exception/RuntimeError
        raise IndexError_(f"{path} is not a readable usearch index: {exc}") from exc
    if not meta:
        raise IndexError_(f"{path} is not a readable usearch index")
    return {
        "dimensions": int(meta["dimensions"]),
        "count": int(meta["count_present"]),
        "dtype": _dtype_name(meta.get("kind_scalar")),
    }


def _dtype_name(kind) -> str:
    """usearch reports a ScalarKind enum (F16, I8, ...); settings use the lowercase names."""
    name = getattr(kind, "name", None) or str(kind)
    return name.split(".")[-1].lower()


class VectorIndex:
    def __init__(self, index: Index, dim: int) -> None:
        self._index = index
        self.dim = dim

    @classmethod
    def empty(cls, dim: int, quantization: str = "f16") -> VectorIndex:
        return cls(Index(ndim=dim, metric="cos", dtype=quantization), dim)

    @classmethod
    def open_or_create(cls, path: Path, dim: int, quantization: str = "f16") -> VectorIndex:
        meta = inspect(path)
        if meta is None:
            return cls.empty(dim, quantization)
        if meta["dimensions"] != dim:
            raise IndexError_(
                f"{path} has dimension {meta['dimensions']}, the active model needs {dim}"
            )
        if meta["dtype"] != quantization:
            raise IndexError_(
                f"{path} is quantised as {meta['dtype']}, the settings say {quantization}"
            )
        try:
            index = Index.restore(str(path), view=False)
        except Exception as exc:  # noqa: BLE001
            raise IndexError_(f"could not load {path}: {exc}") from exc
        if index is None:
            raise IndexError_(f"could not load {path}")
        return cls(index, dim)

    @property
    def count(self) -> int:
        return len(self._index)

    def add(self, keys: Sequence[int], vectors: np.ndarray) -> None:
        if len(keys) == 0:
            return
        arr = np.ascontiguousarray(vectors, dtype=np.float32).reshape(len(keys), self.dim)
        # A re-fingerprinted file keeps its key; usearch refuses duplicate keys, so replace.
        present = [k for k in keys if k in self._index]
        if present:
            self._index.remove(np.asarray(present, dtype=np.uint64))
        # Single-threaded on purpose: with usearch 2.26 a multi-threaded add builds a graph
        # that misses about one exact near-duplicate in a few hundred queries (measured on
        # 60-3000 clustered vectors, f16 and i8 alike); one thread is exact and ~1.5x slower.
        # Recall is the product here; the build runs in a compute child anyway (audit 2026-11-07).
        self._index.add(np.asarray(keys, dtype=np.uint64), arr, copy=True, threads=1)

    def remove(self, keys: Sequence[int]) -> int:
        present = [k for k in keys if k in self._index]
        if not present:
            return 0
        self._index.remove(np.asarray(present, dtype=np.uint64))
        return len(present)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.count == 0 or k <= 0:
            return []
        query = np.ascontiguousarray(vector, dtype=np.float32)
        matches = self._index.search(query, min(k, self.count))
        pairs = [
            (int(key), float(dist))
            for key, dist in zip(matches.keys, matches.distances, strict=True)
        ]
        pairs.sort(key=lambda p: p[1])
        return pairs

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        self._index.save(str(tmp))
        tmp.replace(path)
