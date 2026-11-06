class NotMedia(Exception):
    """The file's content is not an image (or video) despite its extension."""


class Unreadable(Exception):
    """The file is media but cannot be decoded: truncated, corrupt, too large."""
