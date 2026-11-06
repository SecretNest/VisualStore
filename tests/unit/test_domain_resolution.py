from visualstore.domain.resolution import Member, preselect

ARCH1, ARCH2, INBOX = b"\x01" * 16, b"\x02" * 16, b"\x03" * 16


def m(i, folder, role, prio=0, path="x.jpg"):
    return Member(bytes([i]) * 16, folder, role, prio, path)


def test_archive_plus_inbox_keeps_archive_and_auto_executes_identical():
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox"), m(3, INBOX, "inbox")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {
        members[0].file_id: "keep",
        members[1].file_id: "delete",
        members[2].file_id: "delete",
    }
    assert p.status == "auto_resolved" and p.auto_executable


def test_setting_off_disables_auto_execution_entirely():
    """Review Focus 4."""
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox")]
    p = preselect(members, all_identical=True, auto_execute_identical=False)
    assert p.decisions[members[1].file_id] == "delete"
    assert p.status == "open" and not p.auto_executable


def test_inbox_only_keeps_first_by_path_and_is_never_automatic():
    members = [m(1, INBOX, "inbox", path="b/2.jpg"), m(2, INBOX, "inbox", path="A/1.jpg")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {members[1].file_id: "keep", members[0].file_id: "delete"}
    assert p.status == "open" and not p.auto_executable


def test_archive_only_single_folder_is_undecided():
    """Review Focus 4: archive-only groups are never touched automatically."""
    members = [m(1, ARCH1, "archive"), m(2, ARCH1, "archive")]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert set(p.decisions.values()) == {"undecided"}
    assert p.status == "open" and not p.auto_executable


def test_archive_only_several_folders_uses_priority():
    members = [
        m(1, ARCH1, "archive", prio=5),
        m(2, ARCH2, "archive", prio=1),
        m(3, ARCH1, "archive", prio=5),
    ]
    p = preselect(members, all_identical=True, auto_execute_identical=True)
    assert p.decisions == {
        members[1].file_id: "keep",
        members[0].file_id: "delete",
        members[2].file_id: "delete",
    }
    assert p.status == "auto_resolved" and p.auto_executable


def test_non_identical_groups_are_preselected_but_manual():
    members = [m(1, ARCH1, "archive"), m(2, INBOX, "inbox")]
    p = preselect(members, all_identical=False, auto_execute_identical=True)
    assert p.decisions[members[1].file_id] == "delete"
    assert p.status == "open" and not p.auto_executable


def test_propagate_keep_spreads_keep_along_kept_edges():
    from visualstore.domain.resolution import propagate_keep

    decisions = {b"a": "keep", b"b": "delete", b"c": "delete", b"d": "delete"}
    out = propagate_keep(decisions, [(b"a", b"b"), (b"b", b"c"), (b"d", b"d")])
    assert out == {b"a": "keep", b"b": "keep", b"c": "keep", b"d": "delete"}
    assert decisions[b"b"] == "delete"  # input untouched
    assert propagate_keep(decisions, []) == decisions
