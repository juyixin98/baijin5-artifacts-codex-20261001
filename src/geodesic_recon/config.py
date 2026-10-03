"""Deployment settings.

Defaults live in ``config/default.toml``; any key can be overridden with an
environment variable ``GEOREC_<KEY>`` (upper-cased). The boundary rule is a
fixed algorithmic constant and is intentionally not overridable.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .errors import ContractViolation

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default.toml"

#: Fixed neighborhood boundary rule: out-of-bounds neighbors are ignored.
BOUNDARY_RULE = "edge-ignore"

_ENV_PREFIX = "GEOREC_"


@dataclass(frozen=True)
class Settings:
    connectivity: int = 4
    boundary_rule: str = BOUNDARY_RULE
    on_violation: str = "reject"  # "reject" | "clip"
    algorithm: str = "queue"  # "sync" | "queue" | "tiled"
    tile_height: int = 64
    tile_width: int = 64
    max_pixels: int = 4_000_000

    def validate(self) -> "Settings":
        if self.connectivity not in (4, 8):
            raise ContractViolation("BAD_CONNECTIVITY", f"connectivity must be 4 or 8, got {self.connectivity}")
        if self.boundary_rule != BOUNDARY_RULE:
            raise ContractViolation("BAD_BOUNDARY_RULE", f"boundary rule is fixed to {BOUNDARY_RULE!r}")
        if self.on_violation not in ("reject", "clip"):
            raise ContractViolation("BAD_POLICY", f"on_violation must be 'reject' or 'clip', got {self.on_violation!r}")
        if self.algorithm not in ("sync", "queue", "tiled"):
            raise ContractViolation("BAD_ALGORITHM", f"unknown algorithm {self.algorithm!r}")
        if self.tile_height < 1 or self.tile_width < 1:
            raise ContractViolation("BAD_TILE_SHAPE", "tile dimensions must be >= 1")
        if self.max_pixels < 1:
            raise ContractViolation("BAD_SIZE_LIMIT", "max_pixels must be >= 1")
        return self


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    data: dict = {}
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.is_file():
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)

    known = {f.name: f.type for f in fields(Settings)}
    overrides = {}
    for key in known:
        env_key = _ENV_PREFIX + key.upper()
        if env_key in os.environ:
            overrides[key] = os.environ[env_key]

    merged = {**data, **overrides}
    typed = {}
    for key, value in merged.items():
        if key not in known:
            continue
        if key in ("connectivity", "tile_height", "tile_width", "max_pixels"):
            value = int(value)
        typed[key] = value
    return Settings(**typed).validate()
