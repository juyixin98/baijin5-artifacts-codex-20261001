"""Named-profile registry backed by a local directory of ICC files."""

from __future__ import annotations

from pathlib import Path

from ..errors import MissingProfileError
from .validate import ProfileInfo, validate_profile

_PROFILE_SUFFIXES = {".icc", ".icm"}


class ProfileRegistry:
    """Resolves profile names to validated ICC profiles from one directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(
            p.name
            for p in self.directory.iterdir()
            if p.is_file() and p.suffix.lower() in _PROFILE_SUFFIXES
        )

    def _resolve(self, name: str) -> Path:
        if not name or Path(name).name != name:
            # Reject path traversal and absolute paths: names only.
            raise MissingProfileError(
                "profile name must be a plain file name in the profile registry",
                detail={"name": name},
            )
        candidate = self.directory / name
        if not candidate.is_file():
            raise MissingProfileError(
                "named profile not found in the registry",
                detail={"name": name, "available": self.names()},
            )
        return candidate

    def load_bytes(self, name: str) -> bytes:
        return self._resolve(name).read_bytes()

    def load(self, name: str) -> tuple[bytes, ProfileInfo]:
        data = self.load_bytes(name)
        return data, validate_profile(data, source=f"registry:{name}")
