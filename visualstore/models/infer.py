"""ONNX Runtime inference, one lazily created session per process (design §4, §7.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from visualstore.models.registry import ModelError, ModelSpec


class Session:
    def __init__(self, model_path: Path, spec: ModelSpec) -> None:
        self.model_path = Path(model_path)
        self.spec = spec
        self._session = None
        self.input_name: str | None = None

    def _ensure(self):
        if self._session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # children run in parallel; one thread each
            options.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.input_name = self._session.get_inputs()[0].name
        return self._session

    def embed(self, batch: np.ndarray) -> np.ndarray:
        session = self._ensure()
        feed = {self.input_name: np.ascontiguousarray(batch, dtype=np.float32)}
        out = np.asarray(session.run(None, feed)[0], dtype=np.float32).reshape(batch.shape[0], -1)
        if out.shape[1] != self.spec.dim:
            raise ModelError(
                f"model {self.spec.id} produced dimension {out.shape[1]},"
                f" registry says {self.spec.dim}"
            )
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms
