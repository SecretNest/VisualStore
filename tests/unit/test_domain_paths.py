import pytest

from visualstore.domain import paths


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""),
        ("/", ""),
        ("a/b/", "a/b"),
        ("/a//b", "a/b"),
        ("a\\b\\c", "a/b/c"),
        ("./a/./b", "a/b"),
    ],
)
def test_normalise_relative(text, expected):
    assert paths.normalise_relative(text) == expected


def test_parent_segments_are_rejected():
    with pytest.raises(ValueError):
        paths.normalise_relative("a/../b")


def test_split_and_join():
    assert paths.split_relative("a/b/c.jpg") == ("a/b", "c.jpg")
    assert paths.split_relative("c.jpg") == ("", "c.jpg")
    assert paths.join_relative("", "c.jpg") == "c.jpg"
    assert paths.join_relative("a/b", "c.jpg") == "a/b/c.jpg"


def test_is_under_respects_recursive_flag_and_case():
    assert paths.is_under("Thumbs/x", "thumbs", recursive=True)
    assert paths.is_under("thumbs", "Thumbs", recursive=False)
    assert not paths.is_under("thumbs/x", "thumbs", recursive=False)
    assert not paths.is_under("thumbsy", "thumbs", recursive=True)
    assert not paths.is_under("a/thumbs", "thumbs", recursive=True)  # anchored at the root


def test_path_key_folds_ascii_only():
    assert paths.path_key("A/B.JPG") == "a/b.jpg"
    assert paths.path_key("É") == "É"  # sqlite NOCASE folds ASCII only; so do we
