from visualstore.domain.scan import (
    DbFile,
    DiskEntry,
    diff,
    move_candidates,
    resolve_moves,
    should_skip_directory,
)


def entry(path, size=1, mtime=1, ext="jpg"):
    directory, _, name = path.rpartition("/")
    return DiskEntry(directory, name, ext, size, mtime)


def dbfile(id_, path, size=1, mtime=1, sha1=None):
    directory, _, name = path.rpartition("/")
    return DbFile(id_, directory, name, size, mtime, sha1)


def test_diff_classifies_insert_changed_unchanged_missing():
    plan = diff(
        disk=[entry("a/1.jpg"), entry("a/2.jpg", size=9), entry("b/3.jpg")],
        db=[
            dbfile(b"1" * 16, "a/1.jpg"),
            dbfile(b"2" * 16, "a/2.jpg", size=5),
            dbfile(b"4" * 16, "c/4.jpg"),
        ],
    )
    assert plan.unchanged == 1
    assert [e.name for e in plan.insert] == ["3.jpg"]
    assert [(fid, e.size) for fid, e in plan.changed] == [(b"2" * 16, 9)]
    assert [f.name for f in plan.missing] == ["4.jpg"]


def test_diff_matches_paths_case_insensitively_like_the_index():
    """Review Focus 2 (part): a case-only rename on a case-insensitive share is not a new file."""
    plan = diff(disk=[entry("A/Pic.JPG")], db=[dbfile(b"1" * 16, "a/pic.jpg")])
    assert plan.unchanged == 1 and plan.insert == [] and plan.missing == []


def test_mtime_change_alone_marks_changed():
    plan = diff(disk=[entry("x.jpg", mtime=2)], db=[dbfile(b"1" * 16, "x.jpg", mtime=1)])
    assert len(plan.changed) == 1


def test_move_candidates_group_by_size_only_where_sizes_meet():
    plan = diff(
        disk=[
            entry("new/a.jpg", size=10),
            entry("new/b.jpg", size=20),
            entry("new/c.jpg", size=99),
        ],
        db=[
            dbfile(b"a" * 16, "old/a.jpg", size=10),
            dbfile(b"b" * 16, "old/b.jpg", size=20),
            dbfile(b"z" * 16, "old/z.jpg", size=7),
        ],
    )
    cands = move_candidates(plan)
    assert set(cands) == {10, 20}
    assert [f.id for f in cands[10][0]] == [b"a" * 16]
    assert [e.name for e in cands[10][1]] == ["a.jpg"]


def test_resolve_moves_requires_exactly_one_match():
    """Review Focus 2: a unique sha1 match is a move; two identical new copies are not."""
    moves = resolve_moves(
        missing_sha1={b"a" * 16: b"\x0a" * 20, b"b" * 16: b"\x0b" * 20, b"c" * 16: b"\x0c" * 20},
        inserted_sha1={
            ("new", "a.jpg"): b"\x0a" * 20,
            ("new", "b1.jpg"): b"\x0b" * 20,
            ("new", "b2.jpg"): b"\x0b" * 20,
        },
    )
    assert moves == [(b"a" * 16, ("new", "a.jpg"))]


def test_skip_rules():
    ignored = [("Thumbs", True), ("raw", False)]
    assert should_skip_directory(".visualstore-trash", [])
    assert should_skip_directory(".visualstore-trash/2026-11/x", [])
    assert not should_skip_directory("sub/.visualstore-trash", [])  # only at the root
    assert should_skip_directory("thumbs/deep", ignored)
    assert should_skip_directory("raw", ignored)
    assert not should_skip_directory("raw/sub", ignored)
    assert not should_skip_directory("", ignored)


def test_unknown_mtime_with_same_size_is_a_touch_not_a_change():
    unknown = [dbfile(b"1" * 16, "x.jpg", size=5, mtime=0)]
    plan = diff(disk=[entry("x.jpg", size=5, mtime=99)], db=unknown)
    assert plan.changed == [] and plan.unchanged == 1
    assert [(fid, e.mtime) for fid, e in plan.touch] == [(b"1" * 16, 99)]
    plan2 = diff(disk=[entry("x.jpg", size=6, mtime=99)], db=unknown)
    assert len(plan2.changed) == 1 and plan2.touch == []


def test_exact_spelling_change_is_reported_as_renamed():
    plan = diff(disk=[entry("A/Pic.JPG")], db=[dbfile(b"1" * 16, "a/pic.jpg")])
    assert plan.unchanged == 1
    assert [(fid, e.directory, e.name) for fid, e in plan.renamed] == [(b"1" * 16, "A", "Pic.JPG")]


def test_disk_entries_colliding_under_nocase_are_reported_once():
    plan = diff(disk=[entry("x.jpg", size=1), entry("X.jpg", size=2)], db=[])
    assert [e.name for e in plan.insert] == ["x.jpg"]
    assert [e.name for e in plan.collisions] == ["X.jpg"]
