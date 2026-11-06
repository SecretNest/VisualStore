import numpy as np
import pytest

from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from visualstore.models.infer import Session
from visualstore.models.registry import ModelError


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "tiny.onnx"
    build_tiny_model(path, dim=8)
    return path


def test_embed_returns_unit_vectors_per_row(model):
    session = Session(model, tiny_spec(8))
    batch = np.random.default_rng(1).random((3, 3, 16, 16), dtype=np.float32)
    out = session.embed(batch)
    assert out.shape == (3, 8) and out.dtype == np.float32
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    single = session.embed(batch[:1])
    assert np.allclose(single[0], out[0], atol=1e-5)  # batch size does not change results


def test_same_colour_means_give_same_vector(model):
    session = Session(model, tiny_spec(8))
    a = np.full((1, 3, 16, 16), 0.3, dtype=np.float32)
    b = np.full((1, 3, 32, 32), 0.3, dtype=np.float32)  # different size, same means
    assert np.allclose(session.embed(a), session.embed(b), atol=1e-5)


def test_dimension_mismatch_is_a_model_error(model):
    with pytest.raises(ModelError, match="dimension"):
        Session(model, tiny_spec(16)).embed(np.zeros((1, 3, 16, 16), dtype=np.float32))


def test_session_is_created_lazily(model):
    session = Session(model, tiny_spec(8))
    assert session._session is None
    session.embed(np.zeros((1, 3, 16, 16), dtype=np.float32))
    assert session._session is not None
