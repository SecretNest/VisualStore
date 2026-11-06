import pytest

from visualstore.db import fingerprints as fp
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir

A, B = b"\x01" * 16, b"\x02" * 16
M = "sscd-resnet50-512-v1"


@pytest.fixture
def vconn(tmp_path):
    c = connect(tmp_path / "vectors.db")
    migrate(c, migrations_dir("vectors"))
    return c


def vec(x: int) -> bytes:
    return bytes([x]) * 1024  # 512 float16 values


def test_replace_frames_is_idempotent_and_per_model(vconn):
    fp.replace_frames(vconn, A, M, [(0, 0, vec(1)), (1, 500, vec(2))])
    fp.replace_frames(vconn, A, "other-model", [(0, 0, vec(9))])
    rows = fp.frames_of_file(vconn, A, M)
    assert [(r["frame_index"], r["frame_time_ms"]) for r in rows] == [(0, 0), (1, 500)]
    fp.replace_frames(vconn, A, M, [(0, 0, vec(3))])  # fewer frames: old ones go
    assert [bytes(r["vector"])[:1] for r in fp.frames_of_file(vconn, A, M)] == [b"\x03"]
    assert fp.vector_count(vconn, M) == 1 and fp.vector_count(vconn, "other-model") == 1
    assert fp.files_with_vectors(vconn, M) == {A}


def test_keys_are_stable_and_resolve_back(vconn):
    keys = fp.allocate_keys(vconn, A, M, [0, 1])
    again = fp.allocate_keys(vconn, A, M, [0, 1])
    assert keys == again and len(set(keys)) == 2
    other = fp.allocate_keys(vconn, B, M, [0])
    assert set(other).isdisjoint(keys)
    assert fp.resolve_keys(vconn, keys + other) == {
        keys[0]: (A, 0),
        keys[1]: (A, 1),
        other[0]: (B, 0),
    }


def test_delete_marks_keys_for_removal_and_drops_frames(vconn):
    fp.replace_frames(vconn, A, M, [(0, 0, vec(1))])
    [k] = fp.allocate_keys(vconn, A, M, [0])
    fp.delete_for_files(vconn, [A])
    assert fp.frames_of_file(vconn, A, M) == []
    assert fp.resolve_keys(vconn, [k]) == {}  # deleted keys never resolve
    assert fp.pending_removals(vconn, M) == [k]
    fp.drop_keys(vconn, [k])
    assert fp.pending_removals(vconn, M) == []
    # a re-added file gets a fresh key, never the recycled one
    [k2] = fp.allocate_keys(vconn, A, M, [0])
    assert k2 != k


def test_meta_and_iteration(vconn):
    assert fp.get_meta(vconn, "model_id") is None
    fp.set_meta(vconn, "model_id", M)
    fp.set_meta(vconn, "model_id", M)  # upsert
    assert fp.get_meta(vconn, "model_id") == M
    for i, fid in enumerate([A, B]):
        fp.replace_frames(vconn, fid, M, [(0, 0, vec(i + 1))])
        fp.allocate_keys(vconn, fid, M, [0])
    fp.replace_frames(
        vconn, b"\x03" * 16, M, [(0, 0, vec(7))]
    )  # vectors without a key are not exported
    batches = list(fp.iter_vectors(vconn, M, batch=1))
    assert len(batches) == 2 and all(len(b) == 1 for b in batches)
    assert {bytes(r["file_id"]) for b in batches for r in b} == {A, B}


def test_allocate_keys_retires_frames_that_vanished(vconn):
    """Review Focus 4: re-fingerprinting with fewer frames replaces, never appends."""
    fid, model = b"v" * 16, "m"
    fp.replace_frames(vconn, fid, model, [(i, i * 100, b"\x00" * 16) for i in range(4)])
    first = fp.allocate_keys(vconn, fid, model, [0, 1, 2, 3])
    assert len(first) == 4 and fp.pending_removals(vconn, model) == []
    fp.replace_frames(vconn, fid, model, [(i, i * 100, b"\x01" * 16) for i in range(2)])
    second = fp.allocate_keys(vconn, fid, model, [0, 1])
    assert second == first[:2]
    assert fp.pending_removals(vconn, model) == first[2:]
    assert set(fp.resolve_keys(vconn, first)) == set(first[:2])
    assert len(fp.frames_of_file(vconn, fid, model)) == 2


def test_retired_keys_are_never_revived_and_only_dead_rows_are_dropped(vconn):
    """Final review I2: an older match's remove list must never delete a live row."""
    fid, model = b"r" * 16, "m"
    fp.replace_frames(vconn, fid, model, [(i, i * 100, b"\x00" * 16) for i in range(4)])
    first = fp.allocate_keys(vconn, fid, model, [0, 1, 2, 3])
    fp.replace_frames(vconn, fid, model, [(i, i * 100, b"\x01" * 16) for i in range(2)])
    fp.allocate_keys(vconn, fid, model, [0, 1])
    retired = fp.pending_removals(vconn, model)
    assert retired == first[2:]
    fp.replace_frames(vconn, fid, model, [(i, i * 100, b"\x02" * 16) for i in range(4)])
    again = fp.allocate_keys(vconn, fid, model, [0, 1, 2, 3])
    assert again[:2] == first[:2] and not set(again[2:]) & set(retired)  # fresh seqs
    assert fp.pending_removals(vconn, model) == retired  # still to be removed from the index
    fp.drop_keys(vconn, retired + again[2:])  # a stale remove list naming live seqs
    assert fp.pending_removals(vconn, model) == []
    assert set(fp.resolve_keys(vconn, again)) == set(again)  # live rows untouched
