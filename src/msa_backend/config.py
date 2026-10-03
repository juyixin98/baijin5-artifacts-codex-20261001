"""Configuration loading.

Settings come from ``config/settings.yaml`` (path overridable via the
``MSA_BACKEND_CONFIG`` environment variable). The loaded config is frozen
into every run record so results are reproducible from provenance alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "settings.yaml"

VALID_AMBIGUITY_POLICIES = ("uniform_split",)


@dataclass(frozen=True)
class AlgorithmConfig:
    identity_threshold: float
    ambiguity_policy: str
    gap_symbol: str
    conservation_threshold: float
    min_effective_coverage: float
    alphabet: tuple[str, ...]

    def snapshot(self) -> dict:
        return {
            "identity_threshold": self.identity_threshold,
            "ambiguity_policy": self.ambiguity_policy,
            "gap_symbol": self.gap_symbol,
            "conservation_threshold": self.conservation_threshold,
            "min_effective_coverage": self.min_effective_coverage,
            "alphabet": list(self.alphabet),
        }


@dataclass(frozen=True)
class AppConfig:
    name: str
    database_path: Path
    log_level: str
    algorithm: AlgorithmConfig = field(default=None)  # type: ignore[assignment]


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or Path(
        os.environ.get("MSA_BACKEND_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    with open(config_path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    app = raw["app"]
    algo = raw["algorithm"]

    if algo["ambiguity_policy"] not in VALID_AMBIGUITY_POLICIES:
        raise ValueError(
            f"unsupported ambiguity_policy {algo['ambiguity_policy']!r}; "
            f"valid: {VALID_AMBIGUITY_POLICIES}"
        )
    if not 0.0 < algo["conservation_threshold"] <= 1.0:
        raise ValueError("conservation_threshold must be in (0, 1]")
    if algo["min_effective_coverage"] <= 0:
        raise ValueError("min_effective_coverage must be positive")
    if not 0.0 <= algo["identity_threshold"] <= 1.0:
        raise ValueError("identity_threshold must be in [0, 1]")

    algorithm = AlgorithmConfig(
        identity_threshold=float(algo["identity_threshold"]),
        ambiguity_policy=str(algo["ambiguity_policy"]),
        gap_symbol=str(algo["gap_symbol"]),
        conservation_threshold=float(algo["conservation_threshold"]),
        min_effective_coverage=float(algo["min_effective_coverage"]),
        alphabet=tuple(str(b) for b in algo["alphabet"]),
    )
    return AppConfig(
        name=str(app["name"]),
        database_path=_resolve_path(str(app["database_path"])),
        log_level=str(app.get("log_level", "INFO")),
        algorithm=algorithm,
    )
