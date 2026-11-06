"""Profile registry: the map from profile names to directories and open connections.

A profile is the unit of data isolation (design §5.1 Profile, §5.2). The registry is the
only object that knows where a profile's files are; everything else asks it.
"""

from __future__ import annotations

import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from visualstore.clock import now_iso
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir

PROFILE_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProfileError(Exception):
    """Invalid input: bad name, duplicate, wrong confirmation."""


class ProfileNotFound(ProfileError):
    pass


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    display_name: str
    created_at: str
    sort_order: int


@dataclass(frozen=True)
class ProfilePaths:
    root: Path

    @property
    def metadata_db(self) -> Path:
        return self.root / "visualstore.db"

    @property
    def vectors_db(self) -> Path:
        return self.root / "vectors.db"

    @property
    def index(self) -> Path:
        return self.root / "index.usearch"

    @property
    def thumbs(self) -> Path:
        return self.root / "thumbs"


def validate_name(name: str) -> None:
    if not PROFILE_NAME_RE.fullmatch(name or ""):
        raise ProfileError(
            "Profile names use lowercase letters, digits and single hyphens between them."
        )


class ProfileRegistry:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._global: sqlite3.Connection | None = None
        self._metadata: dict[str, sqlite3.Connection] = {}
        self._vectors: dict[str, sqlite3.Connection] = {}

    # -- lifecycle ---------------------------------------------------------------------

    @property
    def global_db_path(self) -> Path:
        return self.data_dir / "global.db"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    def open(self) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._global = connect(self.global_db_path)
        migrate(self._global, migrations_dir("global"))
        for info in self.list():
            self._open_profile(info.name)

    def close(self) -> None:
        for conn in (*self._metadata.values(), *self._vectors.values()):
            conn.close()
        self._metadata.clear()
        self._vectors.clear()
        if self._global is not None:
            self._global.close()
            self._global = None

    def global_conn(self) -> sqlite3.Connection:
        if self._global is None:
            raise RuntimeError("ProfileRegistry is not open")
        return self._global

    def _open_profile(self, name: str) -> None:
        paths = self.paths(name)
        paths.thumbs.mkdir(parents=True, exist_ok=True)
        metadata = connect(paths.metadata_db)
        migrate(metadata, migrations_dir("profile"))
        vectors = connect(paths.vectors_db)
        migrate(vectors, migrations_dir("vectors"))
        self._metadata[name] = metadata
        self._vectors[name] = vectors

    def _close_profile(self, name: str) -> None:
        for store in (self._metadata, self._vectors):
            conn = store.pop(name, None)
            if conn is not None:
                conn.close()

    # -- queries -----------------------------------------------------------------------

    def list(self) -> list[ProfileInfo]:
        rows = self.global_conn().execute(
            "SELECT name, display_name, created_at, sort_order FROM profile"
            " ORDER BY sort_order, created_at"
        )
        return [ProfileInfo(**dict(r)) for r in rows]

    def names(self) -> list[str]:
        return [p.name for p in self.list()]

    def exists(self, name: str) -> bool:
        row = self.global_conn().execute("SELECT 1 FROM profile WHERE name = ?", (name,)).fetchone()
        return row is not None

    def get(self, name: str) -> ProfileInfo:
        row = (
            self.global_conn()
            .execute(
                "SELECT name, display_name, created_at, sort_order FROM profile WHERE name = ?",
                (name,),
            )
            .fetchone()
        )
        if row is None:
            raise ProfileNotFound(f"profile {name!r} does not exist")
        return ProfileInfo(**dict(row))

    def paths(self, name: str) -> ProfilePaths:
        validate_name(name)
        return ProfilePaths(root=self.profiles_dir / name)

    def conn(self, name: str) -> sqlite3.Connection:
        try:
            return self._metadata[name]
        except KeyError:
            raise ProfileNotFound(f"profile {name!r} does not exist") from None

    def vectors_conn(self, name: str) -> sqlite3.Connection:
        try:
            return self._vectors[name]
        except KeyError:
            raise ProfileNotFound(f"profile {name!r} does not exist") from None

    def usage_bytes(self, name: str) -> int:
        """Filesystem only, no database access: safe to run in a worker thread."""
        root = self.paths(name).root
        if not root.is_dir():
            return 0
        return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    # -- mutations ---------------------------------------------------------------------

    def create(self, name: str, display_name: str = "") -> ProfileInfo:
        validate_name(name)
        if self.exists(name):
            raise ProfileError(f"profile {name!r} already exists")
        g = self.global_conn()
        order = g.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM profile").fetchone()[0]
        with g:
            g.execute(
                "INSERT INTO profile (name, display_name, created_at, sort_order)"
                " VALUES (?, ?, ?, ?)",
                (name, display_name or name, now_iso(), order),
            )
        self._open_profile(name)
        return self.get(name)

    def rename(self, name: str, display_name: str) -> ProfileInfo:
        self.get(name)
        g = self.global_conn()
        with g:
            g.execute(
                "UPDATE profile SET display_name = ? WHERE name = ?", (display_name or name, name)
            )
        return self.get(name)

    def delete(self, name: str, confirm_name: str) -> None:
        self.get(name)
        if confirm_name != name:
            raise ProfileError("Type the profile name exactly to confirm deletion.")
        self._close_profile(name)
        root = self.paths(name).root
        if root.exists():
            shutil.rmtree(root)
        g = self.global_conn()
        with g:
            g.execute("DELETE FROM profile WHERE name = ?", (name,))

    # -- secrets -----------------------------------------------------------------------

    def secret(self, key: str) -> bytes:
        """A random 32-byte value created on first use and kept in global.db."""
        g = self.global_conn()
        row = g.execute("SELECT value FROM secret WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return bytes(row["value"])
        value = secrets.token_bytes(32)
        with g:
            g.execute("INSERT INTO secret (key, value) VALUES (?, ?)", (key, value))
        return value
