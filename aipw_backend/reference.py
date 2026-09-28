"""Independent reference estimators.

These estimators use the **known true nuisance functions** of the synthetic
DGP and are implemented from scratch against NumPy only.  They deliberately
import neither :mod:`aipw_backend.kernel` nor
:mod:`aipw_backend.models`, so agreement between the cross-fitted production
estimator and these quantities is evidence about the formula, not a
tautology.

Three independent population-effect estimators are provided:

* :func:`g_formula`     - mean_X {mu1(X) - mu0(X)}
* :func:`ipw`           - mean_i {A_i/e_i Y_i - (1-A_i)/(1-e_i) Y_i}
* :func:`aipw_oracle`   - mean_i psi_i with true e, mu1, mu0

All three target tau; the AIPW influence function with true nuisances also
gives an analytic standard error used to cross-check the production
influence-function SE.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dgp import DGPParams, true_outcome, true_propensity


@dataclass(frozen=True)
class ReferenceEstimate:
    g_formula: float
    ipw: float
    aipw: float
    se_aipw: float
    influence: np.ndarray


def oracle_influence(
    x: np.ndarray, a: np.ndarray, y: np.ndarray, p: DGPParams
) -> np.ndarray:
    e = true_propensity(x, p)
    m1 = true_outcome(x, np.ones(x.shape[0], dtype=np.int8), p)
    m0 = true_outcome(x, np.zeros(x.shape[0], dtype=np.int8), p)
    psi = m1 - m0
    psi = psi + (a / e) * (y - m1)
    psi = psi - ((1.0 - a) / (1.0 - e)) * (y - m0)
    return psi


def reference_estimate(
    x: np.ndarray, a: np.ndarray, y: np.ndarray, p: DGPParams
) -> ReferenceEstimate:
    n = x.shape[0]
    e = true_propensity(x, p)
    m1 = true_outcome(x, np.ones(n, dtype=np.int8), p)
    m0 = true_outcome(x, np.zeros(n, dtype=np.int8), p)

    g_formula = float(np.mean(m1 - m0))
    ipw = float(np.mean((a / e) * y - ((1.0 - a) / (1.0 - e)) * y))
    psi = m1 - m0 + (a / e) * (y - m1) - ((1.0 - a) / (1.0 - e)) * (y - m0)
    aipw = float(np.mean(psi))
    se = float(np.std(psi, ddof=1) / np.sqrt(n))
    return ReferenceEstimate(
        g_formula=g_formula, ipw=ipw, aipw=aipw, se_aipw=se, influence=psi
    )


def naive_mean_difference(a: np.ndarray, y: np.ndarray) -> float:
    """The both-nuisances-wrong limit benchmark (no adjustment)."""
    return float(y[a == 1].mean() - y[a == 0].mean())
