"""A stand-in model file for tests that run the scan path but never infer."""

import json
from pathlib import Path

DEFAULT_MODEL = "sscd-resnet50-512-v1"


def install_fake_model(model_dir: Path, model_id: str = DEFAULT_MODEL, dim: int = 512) -> Path:
    """Register `model_id` as a local-only model in `model_dir` and drop a placeholder file,
    so `resolve_model_path` accepts it without a download or a hash check."""
    model_dir.mkdir(parents=True, exist_ok=True)
    registry = model_dir / "models.json"
    entries = json.loads(registry.read_text()) if registry.is_file() else {}
    entries[model_id] = {"dim": dim, "input_size": 16, "mean": [0, 0, 0], "std": [1, 1, 1]}
    registry.write_text(json.dumps(entries))
    path = model_dir / f"{model_id}.onnx"
    path.write_bytes(b"placeholder")
    return path
