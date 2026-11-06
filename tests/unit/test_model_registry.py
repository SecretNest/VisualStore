import hashlib

import pytest

from visualstore.models import registry
from visualstore.models.registry import ModelError, ModelSpec


def spec(tmp_path, data=b"onnx-bytes", sha="auto", url=None):
    src = tmp_path / "src.onnx"
    src.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest() if sha == "auto" else sha
    return ModelSpec(
        id="tiny-test-8-v1",
        url=url or src.as_uri(),
        sha256=digest,
        dim=8,
        input_size=32,
        mean=(0.5, 0.5, 0.5),
        std=(0.5, 0.5, 0.5),
        description="test",
    )


def test_registry_ships_both_candidates():
    reg = registry.load_registry()
    assert {"sscd-resnet50-512-v1", "dinov2-vits14-384-v1"} <= set(reg)
    s = registry.get_spec("sscd-resnet50-512-v1")
    assert s.dim == 512 and s.input_size == 288 and s.url.startswith("https://")
    with pytest.raises(ModelError):
        registry.get_spec("nope")


def test_download_verifies_and_caches(tmp_path):
    s = spec(tmp_path)
    model_dir = tmp_path / "models"
    path = registry.resolve_model_path(s, model_dir)
    assert path == model_dir / "tiny-test-8-v1.onnx" and path.read_bytes() == b"onnx-bytes"
    assert not list(model_dir.glob("*.part"))
    (tmp_path / "src.onnx").unlink()  # second resolve must not touch the network
    assert registry.resolve_model_path(s, model_dir) == path


def test_bad_download_is_rejected_and_removed(tmp_path):
    s = spec(tmp_path, sha="0" * 64)
    with pytest.raises(ModelError, match="sha256"):
        registry.resolve_model_path(s, tmp_path / "models")
    assert not (tmp_path / "models" / "tiny-test-8-v1.onnx").exists()


def test_existing_file_with_wrong_hash_is_refused_but_kept(tmp_path):
    s = spec(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "tiny-test-8-v1.onnx").write_bytes(b"tampered")
    with pytest.raises(ModelError, match="sha256"):
        registry.resolve_model_path(s, model_dir)
    assert (model_dir / "tiny-test-8-v1.onnx").read_bytes() == b"tampered"


def test_local_only_when_hash_is_unknown(tmp_path):
    s = spec(tmp_path, sha="")
    with pytest.raises(ModelError, match="not downloadable"):
        registry.resolve_model_path(s, tmp_path / "models")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "tiny-test-8-v1.onnx").write_bytes(b"whatever")
    assert registry.resolve_model_path(s, model_dir).read_bytes() == b"whatever"
