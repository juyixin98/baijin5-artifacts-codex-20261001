"""Independent configuration loading and validation.

Config is intentionally plain data parsed from JSON; nothing here imports the
estimation core, keeping configuration side-effect free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import InputError

_VALID_ESTIMANDS = ("ATE", "ATT")


@dataclass(frozen=True)
class FoldConfig:
    n_splits: int = 5
    seed: int = 7301
    stratified: bool = True


@dataclass(frozen=True)
class ModelConfig:
    kind: str
    penalty: float = 1e-6
    max_iter: int = 100
    tol: float = 1e-8


@dataclass(frozen=True)
class AipwConfig:
    folds: FoldConfig = field(default_factory=FoldConfig)
    treatment_model: ModelConfig = field(
        default_factory=lambda: ModelConfig("logistic_ridge")
    )
    outcome_model: ModelConfig = field(
        default_factory=lambda: ModelConfig("ols_ridge")
    )
    propensity_trim: float = 0.0
    max_feature_cells: int = 10_000_000
    estimand: str = "ATE"

    def with_overrides(self, **overrides: object) -> "AipwConfig":
        """Return a new config (immutable update) with top-level overrides."""
        from dataclasses import replace

        cur = {
            "folds": self.folds,
            "treatment_model": self.treatment_model,
            "outcome_model": self.outcome_model,
            "propensity_trim": self.propensity_trim,
            "max_feature_cells": self.max_feature_cells,
            "estimand": self.estimand,
        }
        for key, value in overrides.items():
            if key not in cur:
                raise InputError(f"unknown config key: {key}")
            cur[key] = value
        return replace(self, **cur)


def _require_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{name} must be a number, got {type(value).__name__}")
    return float(value)


def _parse_folds(raw: dict) -> FoldConfig:
    n_splits = raw.get("n_splits", 5)
    if not isinstance(n_splits, int) or isinstance(n_splits, bool) or n_splits < 2:
        raise InputError("folds.n_splits must be an integer >= 2")
    seed = raw.get("seed", 7301)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise InputError("folds.seed must be an integer")
    stratified = raw.get("stratified", True)
    if not isinstance(stratified, bool):
        raise InputError("folds.stratified must be boolean")
    return FoldConfig(n_splits=n_splits, seed=seed, stratified=stratified)


def _parse_model(raw: dict, default_kind: str) -> ModelConfig:
    kind = raw.get("kind", default_kind)
    if kind not in ("logistic_ridge", "ols_ridge", "wrong_constant"):
        raise InputError(
            f"unsupported model kind: {kind!r}",
            details={"allowed": ["logistic_ridge", "ols_ridge", "wrong_constant"]},
        )
    penalty = _require_number("penalty", raw.get("penalty", 1e-6))
    if penalty < 0:
        raise InputError("penalty must be >= 0")
    max_iter = raw.get("max_iter", 100)
    if not isinstance(max_iter, int) or isinstance(max_iter, bool) or max_iter < 1:
        raise InputError("max_iter must be a positive integer")
    tol = _require_number("tol", raw.get("tol", 1e-8))
    if tol <= 0:
        raise InputError("tol must be > 0")
    return ModelConfig(kind=kind, penalty=penalty, max_iter=max_iter, tol=tol)


def validate_config(raw: object) -> AipwConfig:
    """Validate a plain dict parsed from JSON into an :class:`AipwConfig`."""
    if not isinstance(raw, dict):
        raise InputError("configuration must be a JSON object")
    folds = _parse_folds(raw.get("folds", {}))
    treatment_model = _parse_model(
        raw.get("treatment_model", {}), "logistic_ridge"
    )
    outcome_model = _parse_model(raw.get("outcome_model", {}), "ols_ridge")
    trim = _require_number("propensity_trim", raw.get("propensity_trim", 0.0))
    if not 0.0 <= trim < 0.5:
        raise InputError("propensity_trim must be in [0, 0.5)")
    cells = raw.get("max_feature_cells", 10_000_000)
    if not isinstance(cells, int) or isinstance(cells, bool) or cells < 1:
        raise InputError("max_feature_cells must be a positive integer")
    estimand = raw.get("estimand", "ATE")
    if estimand not in _VALID_ESTIMANDS:
        raise InputError(
            f"estimand must be one of {_VALID_ESTIMANDS}",
            details={"got": estimand},
        )
    return AipwConfig(
        folds=folds,
        treatment_model=treatment_model,
        outcome_model=outcome_model,
        propensity_trim=trim,
        max_feature_cells=cells,
        estimand=estimand,
    )


def load_config(path: str | Path) -> AipwConfig:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"config file not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(
            f"config file is not valid JSON: {exc.msg}",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    return validate_config(raw)
