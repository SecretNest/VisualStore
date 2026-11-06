"""Compare-scope rule (design §7.4 step 4): which file pairs may become vector pairs."""

from __future__ import annotations

from dataclasses import dataclass

from visualstore.domain.paths import path_key


@dataclass(frozen=True)
class ScopeInfo:
    folder_id: bytes
    directory: str
    compare_scope: str  # all | other_directories | other_folders


def allowed(a: ScopeInfo, b: ScopeInfo) -> bool:
    if a.folder_id != b.folder_id:
        return True
    if a.compare_scope == "all":
        return True
    if a.compare_scope == "other_folders":
        return False
    return path_key(a.directory) != path_key(b.directory)
