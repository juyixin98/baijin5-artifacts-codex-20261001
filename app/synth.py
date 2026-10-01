"""Synthetic experiment data generator.

Deterministic (seeded) generator producing datasets with *known* ground truth:

    X (pre-treatment baseline) ~ N(0, sigma_x^2)
    T ~ Bernoulli(p)                     (p can deviate -> arm imbalance)
    Y = intercept + tau*T + beta*X + eps (continuous outcome)

Special field types let tests exercise every contract branch:

* ``unrelated``  : pre-treatment covariate independent of Y
* ``post_field`` : a genuinely post-treatment variable Y + noise, also
                   declared not pre-treatment -> leakage screen
* a constant column and a column with injected missing values
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticConfig:
    n: int = 2000
    tau: float = 2.0
    beta: float = 3.0
    intercept: float = 0.5
    sigma_x: float = 1.0
    sigma_eps: float = 1.0
    treatment_prob: float = 0.5
    seed: int = 42


def generate(config: SyntheticConfig, *, add_leakage: bool = True,
             add_constant: bool = True, add_missing: bool = False,
             missing_fraction: float = 0.02) -> dict:
    rng = np.random.default_rng(config.seed)
    x = rng.normal(0.0, config.sigma_x, config.n)
    t = (rng.uniform(size=config.n) < config.treatment_prob).astype(int)
    eps = rng.normal(0.0, config.sigma_eps, config.n)
    y = config.intercept + config.tau * t + config.beta * x + eps
    unrelated = rng.normal(0.0, 1.0, config.n)

    data = {
        "user_id": list(range(config.n)),
        "y": [float(v) for v in y],
        "treatment": [int(v) for v in t],
        "pre_x": [float(v) for v in x],
        "unrelated": [float(v) for v in unrelated],
    }
    pre_decl = {"pre_x": True, "unrelated": True}

    if add_leakage:
        # Post-treatment field: outcome plus noise, only observed after
        # assignment. It mechanically predicts Y and is imbalanced by arm.
        post = y + rng.normal(0.0, 0.5, config.n)
        data["post_spend"] = [float(v) for v in post]
        pre_decl["post_spend"] = False

    if add_constant:
        data["constant"] = [7.0] * config.n
        pre_decl["constant"] = True

    if add_missing:
        rng_m = np.random.default_rng(config.seed + 1)
        col = np.array(data["pre_x"], dtype=float)
        idx = rng_m.choice(config.n, size=int(config.n * missing_fraction),
                           replace=False)
        col[idx] = np.nan
        data["pre_x"] = [None if np.isnan(v) else float(v) for v in col]

    return {
        "name": f"synthetic_n{config.n}_tau{config.tau}_seed{config.seed}",
        "outcome_column": "y",
        "treatment_column": "treatment",
        "covariates": [c for c in ("pre_x", "unrelated", "post_spend", "constant")
                       if c in data],
        "pre_treatment_covariates": pre_decl,
        "data": data,
        "ground_truth": {
            "tau": config.tau,
            "beta": config.beta,
            "intercept": config.intercept,
            "sigma_eps": config.sigma_eps,
            "sigma_x": config.sigma_x,
            "seed": config.seed,
        },
    }
