from visualstore.domain.grouping import PairEdge, components

A, B, C, D, E = (bytes([i]) * 16 for i in range(1, 6))


def sha(a, b, judgment="none"):
    return PairEdge(min(a, b), max(a, b), 0.0, "sha1", judgment)


def vec(a, b, distance, judgment="none"):
    return PairEdge(min(a, b), max(a, b), distance, "vector", judgment)


def test_sha1_edges_form_a_component_and_singletons_are_dropped():
    comps = components([A, B, C, D], [sha(A, B), sha(B, C)], group_distance=0.2)
    assert len(comps) == 1
    [c] = comps
    assert c.members == {A, B, C} and c.has_identical and c.min_distance == 0.0


def test_unrelated_cuts_an_edge_related_keep_does_not():
    comps = components([A, B, C], [sha(A, B, "unrelated"), vec(B, C, 0.1, "related_keep")], 0.2)
    assert [c.members for c in comps] == [{B, C}]
    assert not comps[0].has_identical and comps[0].min_distance == 0.1


def test_vector_edges_beyond_group_distance_do_not_connect():
    comps = components([A, B, C], [vec(A, B, 0.3), vec(B, C, 0.15)], group_distance=0.2)
    assert [c.members for c in comps] == [{B, C}]


def test_null_distance_import_edges_never_connect():
    assert components([A, B], [PairEdge(A, B, None, "import", "none")], 0.5) == []


def test_two_components_sorted_deterministically():
    comps = components([A, B, C, D, E], [sha(D, E), sha(A, B)], 0.2)
    assert [sorted(c.members) for c in comps] == [[A, B], [D, E]]
