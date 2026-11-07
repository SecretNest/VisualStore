"""VisualStore: self-hosted deduplication for image, animated-image and video libraries."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("visualstore")  # the single source is [project].version in pyproject
except PackageNotFoundError:  # running from a checkout without an installed distribution
    __version__ = "0.0.0+unknown"
