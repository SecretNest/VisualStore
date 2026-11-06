"""Smoke test of the published SSCD export: needs libvips, the model file and an opt-in flag."""

import os
import random
from pathlib import Path

import numpy as np
import pytest

from tests.fixtures.vips import require_pyvips

pyvips = require_pyvips()
pytestmark = pytest.mark.skipif(
    os.environ.get("VISUALSTORE_TEST_REAL_MODEL") != "1",
    reason="set VISUALSTORE_TEST_REAL_MODEL=1 and VISUALSTORE_MODEL_DIR",
)


def scene(seed: int, w: int = 320, h: int = 240):
    """A gradient background with a dozen coloured shapes: enough structure for a copy-detection
    model to latch onto, unlike flat colours or noise."""
    rnd = random.Random(seed)
    xyz = pyvips.Image.xyz(w, h)
    img = (
        (xyz[0] * (255 / w))
        .bandjoin([xyz[1] * (255 / h), xyz[0] * 0 + 128])
        .cast("uchar")
        .copy(interpretation="srgb")
    )
    for _ in range(12):
        col = [rnd.randrange(256) for _ in range(3)]
        x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.randrange(10, 60)
        if rnd.random() < 0.5:
            img = img.draw_circle(col, x, y, r, fill=True)
        else:
            img = img.draw_rect(col, x, y, r, r // 2 + 5, fill=True)
    return img


def test_sscd_puts_a_rescaled_copy_close_and_other_images_far(tmp_path):
    from visualstore.media.decode import load_image, to_model_input
    from visualstore.models.infer import Session
    from visualstore.models.registry import get_spec, resolve_model_path

    model_dir = Path(os.environ["VISUALSTORE_MODEL_DIR"])
    spec = get_spec("sscd-resnet50-512-v1", model_dir)
    session = Session(resolve_model_path(spec, model_dir), spec)

    def embed(img, name: str):
        p = tmp_path / name
        img.write_to_file(str(p))
        decoded = load_image(str(p), 100_000_000)
        return session.embed(to_model_input(decoded, spec.input_size, spec.mean, spec.std))[0]

    base = scene(1)
    original = embed(base, "orig.png")
    rescaled = embed(base.resize(0.9), "small.png")
    other = embed(scene(2), "other.png")
    solid = embed(pyvips.Image.black(320, 240, bands=3) + [10, 200, 30], "solid.png")
    d_copy = 1 - float(np.dot(original, rescaled))
    d_other = 1 - float(np.dot(original, other))
    d_solid = 1 - float(np.dot(original, solid))
    assert d_copy < 0.15, d_copy  # near-duplicate band
    assert d_other > 2 * d_copy, (d_copy, d_other)
    assert d_solid > 2 * d_copy, (d_copy, d_solid)
