from visualstore.domain.neighbours import nearest

A, B, C, D = b"a" * 16, b"b" * 16, b"c" * 16, b"d" * 16
MEMBERS = {A: "x/a.jpg", B: "x/b.jpg", C: "x/c.jpg", D: "x/d.jpg"}


def test_nearest_picks_the_smallest_distance_and_treats_sha1_as_zero():
    pairs = [(A, B, 0.1, "none"), (B, C, 0.05, "none"), (A, C, None, "none")]
    got = nearest(MEMBERS, pairs)
    assert got[A] == (C, 0.0)
    assert got[B] == (C, 0.05)
    assert got[C] == (A, 0.0)
    assert got[D] is None  # no usable pair touches it


def test_ties_go_to_the_neighbour_that_sorts_first_by_path_and_unrelated_is_ignored():
    pairs = [(A, C, 0.1, "none"), (A, B, 0.1, "none"), (C, D, 0.01, "unrelated")]
    got = nearest(MEMBERS, pairs)
    assert got[A] == (B, 0.1)
    assert got[D] is None
    assert got[C] == (A, 0.1)


def test_pairs_outside_the_member_set_are_ignored():
    pairs = [(A, b"z" * 16, 0.0, "none"), (A, A, 0.0, "none")]
    assert nearest({A: "a.jpg"}, pairs) == {A: None}
