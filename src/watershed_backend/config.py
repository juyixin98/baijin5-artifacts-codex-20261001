"""Configuration layer: defaults from ``config/default.yaml``, env overrides.

Environment variables take precedence over the YAML file, which takes
precedence over the dataclass defaults.  Everything the kernel and job
runner need that is *not* part of a single request lives here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

import yaml

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "default.yaml"
)


@dataclass(frozen=True)
class Settings:
    default_connectivity: int = 8
    max_pixels: int = 4_000_000
    progress_chunk_pixels: int = 4096
    log_level: str = "INFO"


_ENV_OVERRIDES = {
    "default_connectivity": ("WATERSHED_DEFAULT_CONNECTIVITY", int),
    "max_pixels": ("WATERSHED_MAX_PIXELS", int),
    "progress_chunk_pixels": ("WATERSHED_PROGRESS_CHUNK_PIXELS", int),
    "log_level": ("WATERSHED_LOG_LEVEL", str),
}


def load_settings(
    config_path: str | os.PathLike | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Build settings from YAML file + environment, env winning."""
    environ = os.environ if environ is None else environ
    path = Path(config_path or environ.get("WATERSHED_CONFIG", _DEFAULT_CONFIG_PATH))

    values: dict = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"config file {path} must contain a mapping")
        values.update(raw)

    known = {f.name for f in fields(Settings)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")

    for name, (env_var, caster) in _ENV_OVERRIDES.items():
        if env_var in environ:
            values[name] = caster(environ[env_var])

    return Settings(**values)
