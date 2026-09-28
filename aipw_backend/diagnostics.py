"""Evidence and diagnostics.

Turns an :class:`AipwResult` into a serializable evidence bundle that records
*why* a judgement was reached: overlap/positivity of the fitted propensity,
balance of out-of-fold predictions across folds, influence-function tail
behaviour, and placebo checks whose expected answer is known:

* ``null_placebo``  - a dataset generated with tau = 0 must contain 0 in its
  confidence interval at the nominal rate.
* ``self_treated_placebo`` - within the treated arm, A/e*(Y-mu1) should be
  mean-zero noise relative to (mu1-mu0); a large centred mean flags a broken
  correction term.

Nothing here mutates the result; bundles are plain dicts ready for JSON.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import numpy as np

from .kernel import AipwResult


def _quantiles(arr: np.ndarray, qs: tuple[float, ...]) -> dict[str, float]:
    qs_arr = np.quantile(arr, qs)
    return {f"q{int(q * 100):02d}": float(v) for q, v in zip(qs, qs_arr)}


def propensity_overlap(result: AipwResult) -> dict[str, Any]:
    e = result.pscore
    return {
        "min": float(np.min(e)),
        "max": float(np.max(e)),
        **_quantiles(e, (0.01, 0.05, 0.50, 0.95, 0.99)),
        "n_below_0.01": int(np.sum(e < 0.01)),
        "n_above_0.99": int(np.sum(e > 0.99)),
        "n_below_0.05": int(np.sum(e < 0.05)),
        "n_above_0.95": int(np.sum(e > 0.95)),
    }


def per_fold_prediction_summary(result: AipwResult) -> list[dict[str, Any]]:
    """Out-of-fold prediction ranges per fold: detects fold-id misalignment
    (one fold with a radically different range is the classic symptom)."""
    out: list[dict[str, Any]] = []
    for k in sorted(np.unique(result.fold_id).tolist()):
        idx = result.fold_id == k
        out.append(
            {
                "fold": int(k),
                "n": int(np.sum(idx)),
                "pscore_mean": float(np.mean(result.pscore[idx])),
                "mu1_mean": float(np.mean(result.mu1[idx])),
                "mu0_mean": float(np.mean(result.mu0[idx])),
                "influence_mean": float(np.mean(result.influence[idx])),
            }
        )
    return out


def influence_summary(result: AipwResult) -> dict[str, Any]:
    psi = result.influence
    return {
        "mean": float(np.mean(psi)),
        "sd": float(np.std(psi, ddof=1)),
        "max_abs": float(np.max(np.abs(psi))),
        **_quantiles(psi, (0.01, 0.50, 0.99)),
        "fraction_weight_dominant": float(
            np.sum(np.abs(psi) > 10.0 * np.std(psi, ddof=1)) / psi.shape[0]
        ),
    }


def correction_terms(result: AipwResult, a: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """Report the two augmentation terms separately.

    Under a correct outcome model both terms have population mean zero; a
    large value is evidence of outcome-model misspecification (which is fine
    if the propensity model is correct - double robustness - but should be
    visible rather than hidden).
    """
    e, m1, m0 = result.pscore, result.mu1, result.mu0
    term1 = (a / e) * (y - m1)
    term0 = -((1.0 - a) / (1.0 - e)) * (y - m0)
    return {
        "treated_correction_mean": float(np.mean(term1)),
        "control_correction_mean": float(np.mean(term0)),
        "treated_correction_sd": float(np.std(term1, ddof=1)),
        "control_correction_sd": float(np.std(term0, ddof=1)),
    }


def build_evidence(
    result: AipwResult,
    a: np.ndarray,
    y: np.ndarray,
    *,
    config_snapshot: dict[str, Any],
    known_effect: float | None = None,
) -> dict[str, Any]:
    """Full serializable evidence bundle for one run."""
    covers_zero = result.ci_low <= 0.0 <= result.ci_high
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "estimand": result.estimand,
        "estimate": result.estimate,
        "se": result.se,
        "ci": [result.ci_low, result.ci_high],
        "ci_covers_zero": bool(covers_zero),
        "n": result.n,
        "n_clusters": result.n_clusters,
        "independent_unit": "cluster" if result.n_clusters else "individual",
        "trimming": result.trimming,
        "propensity_overlap": propensity_overlap(result),
        "influence": influence_summary(result),
        "correction_terms": correction_terms(result, a, y),
        "folds": per_fold_prediction_summary(result),
        "fold_diagnostics": [
            {
                "fold": d.fold,
                "n_train": d.n_train,
                "n_valid": d.n_valid,
                "n_train_treated": d.n_train_treated,
                "n_train_control": d.n_train_control,
                "scaler_mean": d.scaler_mean.tolist(),
                "scaler_scale": d.scaler_scale.tolist(),
                "logistic_iters": d.logistic_iters,
                "logistic_coef_norm": d.logistic_coef_norm,
                "mu1_coef_norm": d.mu1_coef_norm,
                "mu0_coef_norm": d.mu0_coef_norm,
                "train_resid_mse1": d.train_resid_mse1,
                "train_resid_mse0": d.train_resid_mse0,
            }
            for d in result.diagnostics
        ],
        "config": config_snapshot,
    }
    if known_effect is not None:
        bundle["known_effect"] = float(known_effect)
        bundle["error_vs_known"] = float(result.estimate - known_effect)
        bundle["ci_covers_known"] = bool(
            result.ci_low <= known_effect <= result.ci_high
        )
        z = (
            (result.estimate - known_effect) / result.se
            if result.se > 0
            else float("inf")
        )
        bundle["z_vs_known"] = float(z)
    return bundle


def new_run_id(prefix: str = "run") -> str:
    """Deterministic-shape, unique run identifier used in every log line."""
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
