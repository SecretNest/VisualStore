import pytest

from visualstore.domain.scope import ScopeInfo, allowed

F1, F2 = b"\x01" * 16, b"\x02" * 16


@pytest.mark.parametrize(
    "scope,dir_a,dir_b,expected",
    [
        ("all", "x", "x", True),
        ("all", "x", "y", True),
        ("other_directories", "x", "x", False),
        ("other_directories", "x", "X", False),  # NOCASE: same directory
        ("other_directories", "x", "x/sub", True),
        ("other_directories", "x", "y", True),
        ("other_folders", "x", "y", False),
        ("other_folders", "x", "x", False),
    ],
)
def test_same_folder_rules(scope, dir_a, dir_b, expected):
    """Review Focus 2."""
    assert allowed(ScopeInfo(F1, dir_a, scope), ScopeInfo(F1, dir_b, scope)) is expected


@pytest.mark.parametrize("scope", ["all", "other_directories", "other_folders"])
def test_different_folders_are_always_allowed(scope):
    assert allowed(ScopeInfo(F1, "x", scope), ScopeInfo(F2, "x", "other_folders")) is True
