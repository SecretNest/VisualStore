import pytest

from visualstore.domain.bands import BANDS, Thresholds, band, band_bounds, thresholds_from

T = Thresholds(near_duplicate=0.15, variant=0.3)


@pytest.mark.parametrize(
    "distance,identical,expected",
    [
        (0.0, True, "identical"),
        (None, True, "identical"),
        (0.0, False, "near_duplicate"),  # distance 0 without a sha1 edge: still a near duplicate
        (0.15, False, "near_duplicate"),
        (0.1500001, False, "variant"),
        (0.3, False, "variant"),
        (0.31, False, "candidate"),
        (None, False, "candidate"),
    ],
)
def test_band_boundaries_are_inclusive_at_the_top(distance, identical, expected):
    assert band(distance, identical, T) == expected


def test_thresholds_from_settings_and_bounds():
    t = thresholds_from({"similarity.near_duplicate": 0.2, "similarity.variant": 0.4})
    assert t == Thresholds(0.2, 0.4)
    assert band_bounds("near_duplicate", t) == (0.0, 0.2)
    assert band_bounds("variant", t) == (0.2, 0.4)
    lo, hi = band_bounds("candidate", t)
    assert lo == 0.4 and hi == float("inf")
    assert BANDS[0] == "identical"
    with pytest.raises(ValueError):
        band_bounds("nope", t)
