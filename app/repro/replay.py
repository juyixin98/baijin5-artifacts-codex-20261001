"""Deterministic replay of Monte-Carlo analyses.

A replay bundle records everything needed to reproduce an approximate run
bit-for-bit: inputs, seed, draw count, library versions and the outputs.
Re-running the bundle must return identical estimates; otherwise the bundle is
not reproducible and the discrepancy is reported rather than hidden.
"""
from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field

import numpy as np

from app.core import kernel

REPLAY_FORMAT_VERSION = "1"


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "replay_format": REPLAY_FORMAT_VERSION,
    }


def _canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def fingerprint(bundle: dict) -> str:
    """Stable hash of a replay bundle (excluding any prior fingerprint)."""
    body = {k: v for k, v in bundle.items() if k != "fingerprint"}
    return hashlib.sha256(_canonical_payload(body).encode("utf-8")).hexdigest()


@dataclass
class ReplayResult:
    reproducible: bool
    original: dict
    replay: dict
    max_abs_difference: float
    discrepancies: list[dict] = field(default_factory=list)
    bundle: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "reproducible": self.reproducible,
            "max_abs_difference": self.max_abs_difference,
            "discrepancies": self.discrepancies,
            "environment": _environment(),
            "bundle": self.bundle,
        }


def _run_mc(differences: list[float], effect: float | None, alpha: float,
            n_draws: int, seed: int, grid: list[float] | None) -> dict:
    d = np.asarray(differences, dtype=float)
    if grid is not None:
        interval_set, p_values, halfwidth = kernel.mc_invert_confidence_set(
            d, alpha=alpha, n_draws=n_draws,
            grid=np.asarray(grid, dtype=float), seed=seed,
        )
        return {
            "kind": "inversion",
            "p_values": [float(x) for x in p_values],
            "confidence_set": interval_set.to_dict(),
            "mc_error_halfwidth": float(halfwidth),
        }
    result = kernel.mc_pvalue(d, effect=float(effect if effect is not None
                                              else 0.0),
                              n_draws=n_draws, seed=seed)
    return {"kind": "pvalue", **result.to_dict()}


def run_and_replay(differences: list[float], n_draws: int, seed: int,
                   effect: float | None = None, alpha: float = 0.05,
                   grid: list[float] | None = None) -> ReplayResult:
    """Run a Monte-Carlo analysis twice with the same seed and compare."""
    original = _run_mc(differences, effect, alpha, n_draws, seed, grid)
    replayed = _run_mc(differences, effect, alpha, n_draws, seed, grid)

    discrepancies: list[dict] = []
    if original["kind"] == "pvalue":
        diff = abs(original["p_value"] - replayed["p_value"])
        if diff != 0.0:
            discrepancies.append({
                "field": "p_value",
                "original": original["p_value"],
                "replay": replayed["p_value"],
            })
    else:
        diff = max(
            (abs(a - b) for a, b in zip(original["p_values"],
                                        replayed["p_values"])),
            default=0.0,
        )
        if original["confidence_set"] != replayed["confidence_set"]:
            discrepancies.append({
                "field": "confidence_set",
                "original": original["confidence_set"],
                "replay": replayed["confidence_set"],
            })

    bundle = {
        "differences": [float(x) for x in differences],
        "effect": effect,
        "alpha": alpha,
        "n_draws": int(n_draws),
        "seed": int(seed),
        "grid": grid,
        "outputs": original,
        "environment": _environment(),
    }
    bundle["fingerprint"] = fingerprint(bundle)

    return ReplayResult(
        reproducible=not discrepancies,
        original=original,
        replay=replayed,
        max_abs_difference=float(diff),
        discrepancies=discrepancies,
        bundle=bundle,
    )
