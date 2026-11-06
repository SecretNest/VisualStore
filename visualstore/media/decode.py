"""Still-image decoding with libvips (design §7.2). Frames for animated/video arrive in M5."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyvips

from visualstore.media.errors import NotMedia, Unreadable

_NOT_MEDIA_MARKERS = ("not a known file format", "is not in a known format", "no loader")

# Media comes from user libraries: refuse libvips loaders that are not hardened against
# hostile input (pdf, svg, magick, ...). Available since libvips 8.13; older builds just skip it.
try:
    pyvips.block_untrusted_set(True)
except AttributeError:  # pragma: no cover - old libvips
    pass


@dataclass
class Decoded:
    image: pyvips.Image
    width: int
    height: int


def load_image(path: str, max_pixels: int) -> Decoded:
    try:
        # Load through a Source so the file name is never parsed for `[option=value]` suffixes.
        # fail_on="truncated": a cut-off file is an error, not silently padded (design §7.2).
        source = pyvips.Source.new_from_file(path)
        img = pyvips.Image.new_from_source(source, "", access="sequential", fail_on="truncated")
    except pyvips.Error as exc:
        message = str(exc)
        if any(marker in message for marker in _NOT_MEDIA_MARKERS):
            raise NotMedia(message) from exc
        raise Unreadable(message) from exc
    if img.width * img.height > max_pixels:
        raise Unreadable(f"{img.width}x{img.height} exceeds the limit of {max_pixels} pixels")
    try:
        img = img.autorot()
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        if img.interpretation != "srgb" or img.bands != 3:
            img = img.colourspace("srgb")
        if img.bands > 3:
            img = img.extract_band(0, n=3)
        if img.format != "uchar":  # 16-bit / float sources: bound the buffer to w*h*3 bytes
            img = img.cast("uchar")
        img = img.copy_memory()  # decode now so truncated data fails here, not later
    except pyvips.Error as exc:
        raise Unreadable(str(exc)) from exc
    return Decoded(image=img, width=img.width, height=img.height)


def to_model_input(decoded: Decoded, size: int, mean, std) -> np.ndarray:
    """Shorter side to `size`, centre crop, CHW float32 normalised, batch dimension 1."""
    img = decoded.image.thumbnail_image(size, height=size, size="both", crop="centre")
    if img.width != size or img.height != size:  # tiny inputs: force the exact shape
        img = img.resize(size / img.width, vscale=size / img.height)
    arr = np.ndarray(
        buffer=img.write_to_memory(), dtype=np.uint8, shape=[img.height, img.width, img.bands]
    )
    arr = arr[:, :, :3].astype(np.float32) / 255.0
    arr = (arr - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None, ...])


def thumbnail_webp(decoded: Decoded, longest: int = 256) -> bytes:
    scale = longest / max(decoded.width, decoded.height)
    img = decoded.image if scale >= 1 else decoded.image.resize(scale)
    return img.write_to_buffer(".webp[Q=80]")
