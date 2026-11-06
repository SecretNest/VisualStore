"""Skip helper: pyvips raises OSError (not ImportError) when libvips is absent."""

import os

import pytest

REQUIRE_MEDIA = os.environ.get("VISUALSTORE_TEST_REQUIRE_MEDIA") == "1"


def require_pyvips():
    """Skip the module when libvips is absent, unless VISUALSTORE_TEST_REQUIRE_MEDIA=1 (CI, the
    test image): then a broken libvips is a failure, never a silent skip."""
    try:
        import pyvips
    except Exception as exc:  # noqa: BLE001 - any failure means no usable libvips here
        if REQUIRE_MEDIA:
            raise RuntimeError(f"libvips is required here but unusable: {exc}") from exc
        pytest.skip(f"libvips not available: {exc}", allow_module_level=True)
    return pyvips
