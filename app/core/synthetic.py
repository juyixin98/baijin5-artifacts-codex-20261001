"""Synthetic experiment data with known ground truth.

The generator is the backbone of reproducible checks: the true average
treatment effect is a fixed parameter, and the pre-treatment covariate has a
known linear relationship with the outcome, so CUPED variance reduction and
coefficient recovery can be asserted against exact values.

Scenarios
---------
* ``balanced``    - Bernoulli(0.5) assignment, informative covariate.
* ``no_correlate``- covariate independent of outcome (no variance to remove).
* ``imbalanced``  - Bernoulli(0.5) assignment retained, but the realized
                    draw is rejection-sampled until a chance covariate
                    imbalance exceeds a threshold (tests adjustment under
                    visibly unbalanced arms without violating randomization).
* ``leakage``     - adds a post-treatment field derived from the outcome;
                    callers must declare it ``pre_treatment=False`` and the
                    estimator is required to reject it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.contracts import CovariateDeclaration


@dataclass(frozen=True)
class SyntheticDataset:
    scenario: str
    unit_id: np.ndarray
    treatment: np.ndarray
    outcome: np.ndarray
    covariates: dict[str, np.ndarray]
    declarations: list[CovariateDeclaration]
    true_effect: float
    true_beta: dict[str, float]
    seed: int
    params: dict[str, Any]

    def n_rows(self) -> int:
        return len(self.outcome)


def _rng(seed: int, run_salt: str = "") -> np.random.Generator:
    if run_salt:
        seed = int((seed * 2654435761 + abs(hash(run_salt)) % 100000) % (2**31 - 1))
    return np.random.default_rng(seed)


def generate(
    scenario: str = "balanced",
    n: int = 2000,
    true_effect: float = 2.0,
    beta_pre: float = 3.0,
    noise_sigma: float = 1.0,
    seed: int = 20260927,
    run_salt: str = "",
    imbalance_smd: float = 0.25,
    missing_fraction: float = 0.0,
) -> SyntheticDataset:
    rng = _rng(seed, run_salt)
    params = {
        "n": n, "true_effect": true_effect, "beta_pre": beta_pre,
        "noise_sigma": noise_sigma, "scenario": scenario,
        "imbalance_smd": imbalance_smd, "missing_fraction": missing_fraction,
    }

    # Pre-treatment covariate, drawn before assignment.
    x_pre = rng.normal(0.0, 1.0, size=n)
    # Uninformative pre-treatment covariate (zero correlation with outcome).
    x_irrelevant = rng.normal(5.0, 1.0, size=n)

    def draw_assignment() -> np.ndarray:
        if scenario == "imbalanced":
            # Rejection sample realized Bernoulli(0.5) draws until the chance
            # covariate imbalance is large. Under genuine randomization a big
            # realized SMD is only attainable at moderate n, so this path is
            # intended for n around 500 (SE of SMD ~= 2/sqrt(n)). The
            # assignment mechanism itself stays 50/50.
            for _ in range(20000):
                t = rng.integers(0, 2, size=n)
                if t.sum() < 5 or (1 - t).sum() < 5:
                    continue
                x1, x0 = x_pre[t == 1], x_pre[t == 0]
                pooled_sd = np.sqrt((x1.var(ddof=1) + x0.var(ddof=1)) / 2)
                smd = (x1.mean() - x0.mean()) / pooled_sd
                if abs(smd) >= imbalance_smd:
                    return t.astype(float)
            raise RuntimeError(
                f"failed to sample imbalanced realization at n={n}, "
                f"threshold={imbalance_smd}; use smaller n or a lower threshold"
            )
        return rng.integers(0, 2, size=n).astype(float)

    t = draw_assignment()

    # Structural outcome: Y = beta_pre * X_pre + tau * T + eps
    eps = rng.normal(0.0, noise_sigma, size=n)
    y = beta_pre * x_pre + true_effect * t + eps

    declarations = [
        CovariateDeclaration(name="x_pre", pre_treatment=True),
        CovariateDeclaration(name="x_irrelevant", pre_treatment=True),
    ]
    covariates = {"x_pre": x_pre, "x_irrelevant": x_irrelevant}

    if scenario == "no_correlate":
        # Outcome carries no covariate signal: rebuild it without x_pre.
        y = true_effect * t + rng.normal(0.0, noise_sigma, size=n)
        params["beta_pre"] = 0.0

    if scenario == "leakage":
        # Post-treatment field: a noisy copy of the realized outcome.
        x_leak = y + rng.normal(0.0, 0.01, size=n)
        covariates["x_post_leak"] = x_leak
        declarations.append(CovariateDeclaration(name="x_post_leak", pre_treatment=False))

    if missing_fraction > 0:
        mask = rng.random(n) < missing_fraction
        x_pre_missing = x_pre.copy()
        x_pre_missing[mask] = np.nan
        covariates["x_pre"] = x_pre_missing

    unit_id = np.array([f"u{i:06d}" for i in range(n)])
    return SyntheticDataset(
        scenario=scenario,
        unit_id=unit_id,
        treatment=t,
        outcome=y,
        covariates=covariates,
        declarations=declarations,
        true_effect=float(true_effect),
        true_beta={"x_pre": float(beta_pre if scenario != "no_correlate" else 0.0)},
        seed=seed,
        params=params,
    )
