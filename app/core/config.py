"""Configuration loading with environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"


@dataclass(frozen=True)
class EstimationConfig:
    hc1_correction: bool = True
    z_alpha: float = 1.959963984540054


@dataclass(frozen=True)
class AppConfig:
    service_name: str
    host: str
    port: int
    sqlite_path: Path
    log_dir: Path
    log_level: str
    synthetic_seed: int
    estimation: EstimationConfig = field(default_factory=EstimationConfig)

    @property
    def absolute_sqlite_path(self) -> Path:
        p = Path(self.sqlite_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_log_dir(self) -> Path:
        p = Path(self.log_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} must contain a YAML mapping")
    return data


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration.

    Resolution order: explicit ``path`` argument, then ``CUPED_CONFIG`` env
    var, then ``config/default.yaml`` shipped with the project.
    """
    cfg_path = Path(path or os.environ.get("CUPED_CONFIG") or DEFAULT_CONFIG_PATH)
    raw = _read_yaml(cfg_path)

    service = raw.get("service", {})
    storage = raw.get("storage", {})
    est = raw.get("estimation", {})
    synthetic = raw.get("synthetic", {})
    logging_cfg = raw.get("logging", {})

    return AppConfig(
        service_name=service.get("name", "cuped-backend"),
        host=service.get("host", "127.0.0.1"),
        port=int(service.get("port", 8000)),
        sqlite_path=Path(storage.get("sqlite_path", "data/cuped.db")),
        log_dir=Path(logging_cfg.get("dir", "logs")),
        log_level=str(logging_cfg.get("level", "INFO")).upper(),
        synthetic_seed=int(synthetic.get("seed", 20260927)),
        estimation=EstimationConfig(
            hc1_correction=bool(est.get("hc1_correction", True)),
            z_alpha=float(est.get("z_alpha", 1.959963984540054)),
        ),
    )
