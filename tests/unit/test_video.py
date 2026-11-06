import pytest

from tests.fixtures.media import make_clip, require_ffmpeg, truncate_file
from tests.fixtures.vips import require_pyvips

require_ffmpeg()
pyvips = require_pyvips()

from visualstore.media.errors import NotMedia, Unreadable  # noqa: E402
from visualstore.media.frames import sample_times  # noqa: E402
from visualstore.media.video import frame_png, frames_at_indices, frames_at_times, probe  # noqa: E402


def test_probe_reads_duration_fps_frames_and_first_keyframe(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    p = probe(str(clip), 30)
    assert abs(p.duration_s - 3.0) < 0.2 and p.fps == 10.0 and p.nb_frames == 30
    assert p.first_keyframe_s == 0.0
    (tmp_path / "text.mp4").write_text("not a video")
    with pytest.raises(NotMedia):
        probe(str(tmp_path / "text.mp4"), 30)


def test_frames_at_times_returns_one_frame_per_time_in_order(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    times = sample_times(probe(str(clip), 30).duration_s, 8, 0.0)
    frames = frames_at_times(str(clip), times, 30, 100_000_000)
    assert [f.time_ms for f in frames] == [0, 1000, 2000]
    assert [f.index for f in frames] == [0, 1, 2]
    assert all((f.image.width, f.image.height, f.image.bands) == (160, 120, 3) for f in frames)
    long_clip = make_clip(tmp_path / "long.mp4", 20, size="64x48")
    p = probe(str(long_clip), 30)
    frames = frames_at_times(
        str(long_clip), sample_times(p.duration_s, 8, p.first_keyframe_s), 30, 100_000_000
    )
    assert len(frames) == 9 and frames[0].time_ms == 0 and frames[-1].time_ms == 18750


def test_frames_at_indices_selects_exact_frames(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    frames = frames_at_indices(str(clip), [0, 10, 29], 30, 100_000_000, fps=10.0)
    assert [f.time_ms for f in frames] == [0, 1000, 2900] and len(frames) == 3


def test_truncated_video_yields_the_decodable_frames_and_no_temp_files(tmp_path, monkeypatch):
    """Review Focus 1."""
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    clip = make_clip(tmp_path / "c.mp4", 6)
    truncate_file(clip, 0.4)
    p = probe(str(clip), 30)
    frames = frames_at_times(
        str(clip), sample_times(p.duration_s, 8, p.first_keyframe_s), 30, 100_000_000
    )
    assert 1 <= len(frames) < 6 and frames[0].time_ms == 0
    assert list((tmp_path / "tmp").iterdir()) == []
    truncate_file(clip, 0.01)
    with pytest.raises((Unreadable, NotMedia)):
        frames_at_times(str(clip), [0.0, 1.0], 30, 100_000_000)


def test_timeout_marks_the_file_unreadable_not_hanging(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    with pytest.raises(Unreadable, match="timed out"):
        frames_at_times(str(clip), [0.0], 0.001, 100_000_000)  # type: ignore[arg-type]


def test_frame_png_returns_a_png(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    data = frame_png(str(clip), 1.0, 30)
    assert data.startswith(b"\x89PNG") and pyvips.Image.new_from_buffer(data, "").width == 160


def test_oversized_frames_are_refused_before_extraction(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", 3)
    with pytest.raises(Unreadable, match="exceeds"):
        frames_at_times(str(clip), [0.0], 30, 1000)
    with pytest.raises(Unreadable, match="exceeds"):
        frames_at_indices(str(clip), [0], 30, 1000, fps=10.0)
    assert probe(str(clip), 30).width == 160 and probe(str(clip), 30).height == 120
