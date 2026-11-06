"""Relative-path rules shared by scan, import and the API (design §5.1 File, §5.3)."""


def normalise_relative(text: str) -> str:
    """'/'-separated, no leading/trailing separators, '' for the root; backslashes become '/'."""
    parts = [p for p in text.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("relative paths may not contain '..'")
    return "/".join(parts)
