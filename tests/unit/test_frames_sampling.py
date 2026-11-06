from visualstore.media.frames import sample_indices, sample_times, sampling_params


def test_sample_indices_takes_all_up_to_the_cap_then_spreads_evenly():
    assert sample_indices(0, 8) == []
    assert sample_indices(1, 8) == [0]
    assert sample_indices(8, 8) == list(range(8))
    assert sample_indices(9, 8) == [0, 1, 2, 3, 5, 6, 7, 8]
    assert sample_indices(500, 8) == [0, 71, 143, 214, 285, 356, 428, 499]
    assert sample_indices(500, 1) == [0]
    spread = sample_indices(1000, 8)
    assert spread == sorted(set(spread)) and spread[0] == 0 and spread[-1] == 999


def test_sample_times_one_per_second_up_to_the_cap_then_midpoints_plus_keyframe():
    assert sample_times(0, 8, None) == [0.0]
    assert sample_times(-1, 8, 0.0) == [0.0]
    assert sample_times(1.5, 8, None) == [0.0, 1.0]
    assert sample_times(8.0, 8, None) == [float(t) for t in range(8)]
    assert sample_times(20.0, 8, None) == [1.25, 3.75, 6.25, 8.75, 11.25, 13.75, 16.25, 18.75]
    assert sample_times(20.0, 8, 0.0) == [0.0, 1.25, 3.75, 6.25, 8.75, 11.25, 13.75, 16.25, 18.75]
    assert sample_times(20.0, 8, 1.26) == [
        1.25,
        3.75,
        6.25,
        8.75,
        11.25,
        13.75,
        16.25,
        18.75,
    ]  # within 50 ms
    assert sample_times(10.0, 2, 4.0) == [2.5, 4.0, 7.5]
    assert all(round(t * 1000) == t * 1000 for t in sample_times(7.777, 3, 0.3333))


def test_sampling_params_reads_the_settings_with_defaults():
    assert sampling_params({}) == {"max_per_file": 8, "ffmpeg_timeout_s": 120}
    assert sampling_params({"frames.max_per_file": 3, "media.ffmpeg_timeout_s": 9}) == {
        "max_per_file": 3,
        "ffmpeg_timeout_s": 9,
    }
