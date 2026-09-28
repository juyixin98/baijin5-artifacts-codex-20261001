"""Configuration loading (YAML) with explicit validation at the boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"

_OVERLAP_POLICIES = frozenset({"reject", "temp_copy"})


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(frozen=True)
class StorageConfig:
    max_tensors: int = 4096
    max_graphs: int = 256
    max_elements: int = 100_000_000


@dataclass(frozen=True)
class SemanticsConfig:
    overlap_write_policy: str = "reject"


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    storage: StorageConfig
    semantics: SemanticsConfig
    source_path: str | None = None

    def with_overrides(self, **overrides: Any) -> "Config":
        """Return a new config; nested keys use dotted form, e.g. ``server.port``."""
        server = self.server
        storage = self.storage
        semantics = self.semantics
        for dotted, value in overrides.items():
            section, _, field = dotted.partition(".")
            if not field:
                raise ConfigError(f"override must be of form 'section.key', got {dotted!r}")
            if section == "server":
                server = replace(server, **{field: value})
            elif section == "storage":
                storage = replace(storage, **{field: value})
            elif section == "semantics":
                semantics = replace(semantics, **{field: value})
            else:
                raise ConfigError(f"unknown config section {section!r}")
        return Config(server=server, storage=storage, semantics=semantics,
                      source_path=self.source_path)


def _require_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer, got {value!r}")
    return value


def _coerce(raw: dict[str, Any], source_path: str | None) -> Config:
    try:
        server_raw = raw.get("server", {}) or {}
        storage_raw = raw.get("storage", {}) or {}
        semantics_raw = raw.get("semantics", {}) or {}

        server = ServerConfig(
            host=str(server_raw.get("host", "127.0.0.1")),
            port=_require_positive_int(server_raw.get("port", 8000), "server.port"),
        )
        storage = StorageConfig(
            max_tensors=_require_positive_int(
                storage_raw.get("max_tensors", 4096), "storage.max_tensors"),
            max_graphs=_require_positive_int(
                storage_raw.get("max_graphs", 256), "storage.max_graphs"),
            max_elements=_require_positive_int(
                storage_raw.get("max_elements", 100_000_000),
                "storage.max_elements"),
        )
        policy = semantics_raw.get("overlap_write_policy", "reject")
        if policy not in _OVERLAP_POLICIES:
            raise ConfigError(
                f"semantics.overlap_write_policy must be one of "
                f"{sorted(_OVERLAP_POLICIES)}, got {policy!r}")
        semantics = SemanticsConfig(overlap_write_policy=policy)
    except ConfigError:
        raise
    except (TypeError, AttributeError) as exc:
        raise ConfigError(f"malformed config document: {exc}") from exc

    return Config(server=server, storage=storage, semantics=semantics,
                  source_path=source_path)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate configuration from a YAML file.

    ``path`` defaults to the bundled ``config/default.yaml``.
    """
    resolved = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {resolved}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {resolved}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"top-level config document must be a mapping, got {type(raw).__name__}")
    return _coerce(raw, str(resolved))
