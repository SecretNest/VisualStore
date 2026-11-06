import numpy as np
import pytest

from visualstore.index.usearch_index import IndexError_, VectorIndex, inspect


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_add_search_remove_round_trip(tmp_path):
    idx = VectorIndex.empty(4)
    idx.add(
        [10, 11, 12], np.stack([unit([1, 0, 0, 0]), unit([0.9, 0.1, 0, 0]), unit([0, 0, 1, 0])])
    )
    hits = idx.search(unit([1, 0, 0, 0]), k=3)
    assert [k for k, _ in hits] == [10, 11, 12]
    assert hits[0][1] < 1e-3 and 0 < hits[1][1] < 0.02 and abs(hits[2][1] - 1.0) < 1e-3
    assert idx.remove([11, 999]) == 1 and idx.count == 2
    path = tmp_path / "index.usearch"
    idx.save(path)
    assert path.is_file() and not (tmp_path / "index.usearch.tmp").exists()
    again = VectorIndex.open_or_create(path, 4)
    assert again.count == 2 and [k for k, _ in again.search(unit([0, 0, 1, 0]), 1)] == [12]
    assert inspect(path) == {"dimensions": 4, "count": 2}


def test_missing_file_creates_empty_and_dimension_mismatch_is_an_error(tmp_path):
    path = tmp_path / "new.usearch"
    assert inspect(path) is None
    idx = VectorIndex.open_or_create(path, 8)
    assert idx.count == 0 and idx.dim == 8
    idx.add([1], unit(np.arange(1, 9))[None, :])
    idx.save(path)
    with pytest.raises(IndexError_, match="dimension"):
        VectorIndex.open_or_create(path, 16)


def test_corrupt_file_is_an_index_error(tmp_path):
    path = tmp_path / "bad.usearch"
    path.write_bytes(b"not an index at all" * 10)
    with pytest.raises(IndexError_):
        VectorIndex.open_or_create(path, 4)
    with pytest.raises(IndexError_):
        inspect(path)


def test_search_on_empty_index_returns_nothing():
    assert VectorIndex.empty(4).search(unit([1, 0, 0, 0]), 5) == []
