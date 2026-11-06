import numpy as np
import pytest

from visualstore.index import pca


def unit_rows(n, dim, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, dim)).astype(np.float32)
    v[:, : dim // 4] *= 5  # a few dominant directions, so PCA has something to keep
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_fit_project_round_trip_keeps_neighbours(tmp_path):
    vectors = unit_rows(2000, 64)
    p = pca.fit(vectors, 16, sample=500)
    assert p.dims == 16 and p.components.shape == (16, 64) and p.mean.shape == (64,)
    projected = pca.project(p, vectors)
    assert projected.shape == (2000, 16)
    assert np.allclose(np.linalg.norm(projected, axis=1), 1.0, atol=1e-4)
    # nearest neighbour of row 0 in the original space survives the projection most of the time
    exact = np.argsort(-(vectors @ vectors[0]))[1:6]
    reduced = np.argsort(-(projected @ projected[0]))[1:6]
    assert len(set(exact) & set(reduced)) >= 2
    pca.save(p, tmp_path / "pca.npz")
    loaded = pca.load(tmp_path / "pca.npz")
    assert np.allclose(loaded.components, p.components) and np.allclose(loaded.mean, p.mean)


def test_fit_rejects_impossible_dims():
    vectors = unit_rows(50, 8)
    with pytest.raises(ValueError):
        pca.fit(vectors, 8, sample=50)
    with pytest.raises(ValueError):
        pca.fit(vectors, 0, sample=50)
    with pytest.raises(ValueError):
        pca.fit(vectors[:3], 4, sample=50)  # fewer rows than dims


def test_measure_reports_every_variant_with_sane_numbers():
    from scripts.measure_index import measure

    rows = measure(unit_rows(500, 32, seed=3), 5, 8, queries=50)
    assert [r["variant"] for r in rows] == ["f16", "i8", "f16+pca8", "i8+pca8"]
    assert all(r["bytes"] > 0 and 0.0 <= r["recall@5"] <= 1.0 for r in rows)
    assert rows[0]["recall@5"] >= 0.9
