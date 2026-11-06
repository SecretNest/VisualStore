"""Model registry and artefact resolution (ADR-0005)."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_FILE = Path(__file__).parent / "models.json"


class ModelError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    url: str
    sha256: str
    dim: int
    input_size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    description: str

    def file_name(self) -> str:
        return f"{self.id}.onnx"


def load_registry() -> dict[str, ModelSpec]:
    raw = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
    return {
        model_id: ModelSpec(
            id=model_id,
            url=v["url"],
            sha256=v.get("sha256", ""),
            dim=int(v["dim"]),
            input_size=int(v["input_size"]),
            mean=tuple(v["mean"]),
            std=tuple(v["std"]),
            description=v.get("description", ""),
        )
        for model_id, v in raw.items()
    }


def get_spec(model_id: str) -> ModelSpec:
    try:
        return load_registry()[model_id]
    except KeyError:
        raise ModelError(f"unknown model id {model_id!r}") from None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def job_params(spec: ModelSpec, model_dir: Path) -> dict:
    """What a compute child needs to know about the model: plain data, no registry access."""
    return {
        "id": spec.id,
        "path": str(model_dir / spec.file_name()),
        "dim": spec.dim,
        "input_size": spec.input_size,
        "mean": list(spec.mean),
        "std": list(spec.std),
    }


def resolve_model_path(spec: ModelSpec, model_dir: Path) -> Path:
    """The verified local file for a model, downloading it when allowed and absent."""
    target = model_dir / spec.file_name()
    if target.is_file():
        if spec.sha256 and sha256_of(target) != spec.sha256:
            raise ModelError(
                f"{target} does not match the registered sha256 for {spec.id}; replace or remove it"
            )
        return target
    if not spec.sha256:
        raise ModelError(
            f"model {spec.id} has no registered sha256 and is therefore not downloadable;"
            f" place {spec.file_name()} in {model_dir} by hand"
        )
    model_dir.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(spec.url, timeout=60) as resp, open(part, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        if sha256_of(part) != spec.sha256:
            raise ModelError(f"downloaded {spec.id} failed the sha256 check; download discarded")
        part.replace(target)
    except ModelError:
        part.unlink(missing_ok=True)
        raise
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise ModelError(f"could not download {spec.id} from {spec.url}: {exc}") from exc
    return target
