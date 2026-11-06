"""Skip helper: pyvips raises OSError (not ImportError) when libvips is absent."""

import pytest


def require_pyvips():
    try:
        import pyvips
    except Exception as exc:  # noqa: BLE001 - any failure means no usable libvips here
        pytest.skip(f"libvips not available: {exc}", allow_module_level=True)
    return pyvips
