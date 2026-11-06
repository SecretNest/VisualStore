import numpy as np
import pytest

from tests.fixtures.images import garbage, png_bytes, png_header_only, truncated
from tests.fixtures.vips import require_pyvips

pyvips = require_pyvips()

from visualstore.media.decode import load_image, thumbnail_webp, to_model_input  # noqa: E402
from visualstore.media.errors import NotMedia, Unreadable  # noqa: E402

MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_decode_and_model_input_shape_and_normalisation(tmp_path):
    d = load_image(
        write(tmp_path, "g.png", png_bytes(64, 40, gradient=True)), max_pixels=10_000_000
    )
    assert (d.width, d.height) == (64, 40)
    x = to_model_input(d, 32, MEAN, STD)
    assert x.shape == (1, 3, 32, 32) and x.dtype == np.float32
    assert -3 < x.min() < x.max() < 3  # normalised, not raw 0..255


def test_solid_colour_survives_resize(tmp_path):
    d = load_image(
        write(tmp_path, "r.png", png_bytes(50, 50, rgb=(255, 0, 0))), max_pixels=10_000_000
    )
    x = to_model_input(d, 16, (0, 0, 0), (1, 1, 1))  # identity normalisation: values in 0..1
    assert np.allclose(x[0, 0], 1.0, atol=0.02) and np.allclose(x[0, 1], 0.0, atol=0.02)


def test_thumbnail_is_webp_and_bounded(tmp_path):
    d = load_image(write(tmp_path, "big.png", png_bytes(300, 120)), max_pixels=10_000_000)
    data = thumbnail_webp(d, 64)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    thumb = pyvips.Image.new_from_buffer(data, "")
    assert max(thumb.width, thumb.height) == 64


def test_garbage_with_image_extension_is_not_media(tmp_path):
    """Review Focus 4."""
    with pytest.raises(NotMedia):
        load_image(write(tmp_path, "renamed.jpg", garbage()), max_pixels=10_000_000)


def test_truncated_and_oversized_are_unreadable(tmp_path):
    """Review Focus 4."""
    with pytest.raises(Unreadable):
        load_image(
            write(tmp_path, "cut.png", truncated(png_bytes(200, 200))), max_pixels=10_000_000
        )
    with pytest.raises(Unreadable, match="pixels"):
        load_image(write(tmp_path, "big.png", png_bytes(200, 200)), max_pixels=1_000)
    with pytest.raises(Unreadable):  # a header announcing 20000x20000 with no pixel data
        load_image(write(tmp_path, "header.png", png_header_only(20000, 20000)), max_pixels=10**9)


def test_one_by_one_image_decodes(tmp_path):
    d = load_image(write(tmp_path, "dot.png", png_bytes(1, 1)), max_pixels=10)
    assert to_model_input(d, 8, MEAN, STD).shape == (1, 3, 8, 8)


def test_option_suffix_in_a_real_file_name_is_not_parsed(tmp_path):
    """Security review: libvips parses `name[opt=v]`; a file literally named that way must load."""
    path = write(tmp_path, "tricky.png[page=9]", png_bytes(12, 12))
    d = load_image(path, max_pixels=10_000_000)
    assert (d.width, d.height) == (12, 12)
