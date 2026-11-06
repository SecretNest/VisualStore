from tests.fixtures.images import png_bytes
from tests.fixtures.media import make_apng, make_gif, make_webp_anim, require_ffmpeg
from tests.fixtures.vips import require_pyvips

require_ffmpeg()
pyvips = require_pyvips()

from visualstore.media.animated import backend, frames, load_pages, page_count, page_times_ms  # noqa: E402
from visualstore.media.frames import sample_indices  # noqa: E402

MAX = 100_000_000


def test_backend_by_suffix():
    assert backend("/x/a.gif") == "vips" and backend("/x/a.webp") == "vips"
    assert backend("/x/a.APNG") == "ffmpeg" and backend("/x/a.png") == "vips"


def test_page_count_and_times_for_gif_webp_and_a_still(tmp_path):
    gif = make_gif(tmp_path / "a.gif", 10)
    assert page_count(str(gif), MAX) == 10
    assert page_times_ms(str(gif)) == [i * 200 for i in range(10)]
    webp = make_webp_anim(tmp_path / "a.webp", 4)
    assert page_count(str(webp), MAX) == 4 and page_times_ms(str(webp))[:2] == [0, 200]
    still = tmp_path / "s.png"
    still.write_bytes(png_bytes(20, 10))
    assert page_count(str(still), MAX) == 1 and page_times_ms(str(still)) == [0]


def test_load_pages_returns_the_requested_pages_as_single_frames(tmp_path):
    gif = make_gif(tmp_path / "a.gif", 10)
    got = load_pages(str(gif), [0, 4, 9], MAX)
    assert [(f.index, f.time_ms) for f in got] == [(0, 0), (1, 800), (2, 1800)]
    assert all((f.image.width, f.image.height, f.image.bands) == (64, 48, 3) for f in got)


def test_frames_follow_the_sampling_rules_for_each_backend(tmp_path):
    """Review Focus 2: 1-frame gif → 1, 10-frame gif → 8, 500-frame apng → 8, increasing times."""
    one = make_gif(tmp_path / "one.gif", 1)
    assert [f.time_ms for f in frames(str(one), 8, 60, MAX)] == [0]
    ten = make_gif(tmp_path / "ten.gif", 10)
    got = frames(str(ten), 8, 60, MAX)
    assert len(got) == 8 and [f.index for f in got] == list(range(8))
    assert [f.time_ms for f in got] == [i * 200 for i in sample_indices(10, 8)]
    apng = make_apng(tmp_path / "big.apng", 500)
    got = frames(str(apng), 8, 120, MAX)
    times = [f.time_ms for f in got]
    assert len(got) == 8 and times == sorted(times) and len(set(times)) == 8 and times[0] == 0
    assert times[-1] == round(499 / 5 * 1000)
    assert [f.time_ms for f in frames(str(ten), 3, 60, MAX)] == [0, 800, 1800]


def test_pages_are_shrunk_to_at_most_1024_px(tmp_path):
    big = make_gif(tmp_path / "big.gif", 2, size="2048x1024")
    [frame] = load_pages(str(big), [1], MAX)
    assert (frame.image.width, frame.image.height) == (1024, 512)
