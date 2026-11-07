from visualstore.domain.bulk import build_plan, plan_group, signature
from visualstore.domain.resolution import Member

G = b"g" * 16
ARCH, INBOX, ARCH2 = b"A" * 16, b"I" * 16, b"B" * 16
NAMES = {ARCH: "Arch", INBOX: "Inbox", ARCH2: "Arch2"}


def m(fid, folder, role, path, priority=0):
    return Member(fid, folder, role, priority, path)


def test_signature_is_order_independent():
    assert signature([b"b" * 16, b"a" * 16]) == signature([b"a" * 16, b"b" * 16])
    assert signature([b"a" * 16]) != signature([b"b" * 16])


def test_archive_plus_inbox_deletes_the_inbox_files():
    members = [
        m(b"1" * 16, ARCH, "archive", "a.jpg"),
        m(b"2" * 16, INBOX, "inbox", "b.jpg"),
        m(b"3" * 16, INBOX, "inbox", "c.jpg"),
    ]
    plan = plan_group(G, members, NAMES)
    assert plan is not None
    assert set(plan.delete) == {b"2" * 16, b"3" * 16} and plan.keep == (b"1" * 16,)
    assert plan.folders == ("Inbox",) and plan.signature == signature(
        [b"1" * 16, b"2" * 16, b"3" * 16]
    )


def test_inbox_only_keeps_the_first_by_path_and_archive_only_is_skipped():
    inbox = [m(b"2" * 16, INBOX, "inbox", "z.jpg"), m(b"1" * 16, INBOX, "inbox", "a.jpg")]
    plan = plan_group(G, inbox, NAMES)
    assert plan is not None and plan.keep == (b"1" * 16,) and plan.delete == (b"2" * 16,)
    archive = [m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, ARCH, "archive", "b.jpg")]
    assert plan_group(G, archive, NAMES) is None  # undecided: skipped
    two_archives = [
        m(b"1" * 16, ARCH, "archive", "a.jpg", 1),
        m(b"2" * 16, ARCH2, "archive", "b.jpg", 2),
    ]
    plan = plan_group(G, two_archives, NAMES)
    assert plan is not None and plan.delete == (b"2" * 16,) and plan.folders == ("Arch2",)


def test_build_plan_counts_and_tokens():
    p1 = plan_group(
        b"1" * 16,
        [m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, INBOX, "inbox", "b.jpg")],
        NAMES,
    )
    p2 = plan_group(
        b"2" * 16,
        [m(b"3" * 16, INBOX, "inbox", "c.jpg"), m(b"4" * 16, INBOX, "inbox", "d.jpg")],
        NAMES,
    )
    plan = build_plan(3, [p1, None, p2])
    assert plan.matched == 3 and plan.skipped == 1 and len(plan.groups) == 2
    assert plan.files_to_delete == 2 and plan.folders == ("Inbox",)
    assert plan.token == build_plan(3, [p2, None, p1]).token  # order independent
    assert plan.token != build_plan(3, [p1]).token
    empty = build_plan(0, [])
    assert empty.groups == () and empty.files_to_delete == 0 and empty.token


def test_a_related_keep_edge_that_overrides_the_rules_makes_the_group_skipped():
    """Final review C1: a human said keep both; bulk must not delete one of them."""
    members = [
        m(b"1" * 16, ARCH, "archive", "a.jpg"),
        m(b"2" * 16, INBOX, "inbox", "b.jpg"),
        m(b"3" * 16, INBOX, "inbox", "c.jpg"),
    ]
    assert plan_group(G, members, NAMES, kept_edges=[(b"1" * 16, b"2" * 16)]) is None
    # an edge between two members the rules keep anyway changes nothing
    two_archives = [
        m(b"1" * 16, ARCH, "archive", "a.jpg"),
        m(b"4" * 16, ARCH, "archive", "d.jpg"),
        m(b"2" * 16, INBOX, "inbox", "b.jpg"),
    ]
    plan = plan_group(G, two_archives, NAMES, kept_edges=[(b"1" * 16, b"4" * 16)])
    assert plan is not None and plan.delete == (b"2" * 16,)


def test_token_changes_when_the_delete_set_changes_for_the_same_members():
    """Final review I2: a Folder role change between preview and execute must invalidate the token."""
    a, b = m(b"1" * 16, ARCH, "archive", "a.jpg"), m(b"2" * 16, INBOX, "inbox", "b.jpg")
    before = build_plan(1, [plan_group(G, [a, b], NAMES)])
    swapped = [m(b"1" * 16, INBOX, "inbox", "a.jpg"), m(b"2" * 16, ARCH, "archive", "b.jpg")]
    after = build_plan(1, [plan_group(G, swapped, NAMES)])
    assert before.groups[0].signature == after.groups[0].signature
    assert before.token != after.token
