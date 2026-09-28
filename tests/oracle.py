"""Independent reference oracle used ONLY by tests.

This is deliberately written through different numerical paths than the
package under test, so agreement between the two is evidence rather than a
tautology:

* logistic regression  : ``scipy.optimize.minimize`` (L-BFGS-B, analytic
  gradient) instead of the package's hand-rolled IRLS;
* outcome regression   : ``numpy.linalg.lstsq`` on RAW (unstandardized)
  columns instead of the package's standardized ridge solve;
* folds                : supplied by the caller (partition properties are
  tested independently); the oracle never imports ``aipw.crossfit``;
* AIPW point & var     : explicit Python loops accumulating Horvitz-Thompson
  contributions and a Bessel-corrected variance.

Nothing in ``src/aipw`` is imported here except the ``Dataset`` container.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from aipw.contract import Dataset


def _design(x: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(x.shape[0]), x])


def ref_logistic_propensity(x_train: np.ndarray, a_train: np.ndarray,
                            x_valid: np.ndarray) -> np.ndarray:
    d = x_train.shape[1] + 1
    z = _design(x_train)

    def negll(beta: np.ndarray) -> float:
        eta = z @ beta
        # log-sum-exp stable log-loss
        loss = np.sum(np.logaddexp(0.0, eta) - a_train * eta)
        return float(loss)

    def grad(beta: np.ndarray) -> np.ndarray:
        p = 1.0 / (1.0 + np.exp(-(z @ beta)))
        return z.T @ (p - a_train)

    opt = minimize(negll, np.zeros(d), jac=grad, method="L-BFGS-B",
                   options={"maxiter": 2000, "ftol": 1e-13, "gtol": 1e-10})
    if not opt.success:
        raise RuntimeError(f"oracle logistic failed: {opt.message}")
    zv = _design(x_valid)
    return 1.0 / (1.0 + np.exp(-(zv @ opt.x)))


def ref_ols(x_train: np.ndarray, y_train: np.ndarray,
            x_valid: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(_design(x_train), y_train, rcond=None)
    return _design(x_valid) @ beta


def ref_oof_arrays(dataset: Dataset, fold_id: np.ndarray, k: int):
    n = dataset.n
    e = np.empty(n)
    m0 = np.empty(n)
    m1 = np.empty(n)
    for f in range(k):
        va = fold_id == f
        tr = ~va
        xt, at, yt = dataset.x[tr], dataset.a[tr], dataset.y[tr]
        e[va] = ref_logistic_propensity(xt, at, dataset.x[va])
        m0[va] = ref_ols(xt[at == 0], yt[at == 0], dataset.x[va])
        m1[va] = ref_ols(xt[at == 1], yt[at == 1], dataset.x[va])
    return e, m0, m1


def ref_aipw_ate(dataset: Dataset, fold_id: np.ndarray, k: int,
                 lo: float = 0.01, hi: float = 0.99) -> dict[str, float]:
    """Explicit-loop HT AIPW point, component points and influence variance."""
    e_raw, m0, m1 = ref_oof_arrays(dataset, fold_id, k)
    e = np.clip(e_raw, lo, hi)
    n = dataset.n

    total = 0.0
    gcomp_total = 0.0
    ipw_total = 0.0
    phi = np.empty(n)
    for i in range(n):
        ai, yi = dataset.a[i], dataset.y[i]
        aug0 = (1.0 - ai) * (yi - m0[i]) / (1.0 - e[i])
        aug1 = ai * (yi - m1[i]) / e[i]
        phi[i] = m1[i] - m0[i] + aug1 - aug0
        total += phi[i]
        gcomp_total += m1[i] - m0[i]
        ipw_total += ai * yi / e[i] - (1.0 - ai) * yi / (1.0 - e[i])

    point = total / n
    var = float(np.sum((phi - point) ** 2) / (n * (n - 1)))
    return {
        "point": float(point),
        "gcomp": float(gcomp_total / n),
        "ipw": float(ipw_total / n),
        "se": float(np.sqrt(var)),
        "prop_min_raw": float(e_raw.min()),
        "prop_max_raw": float(e_raw.max()),
    }


def ref_cluster_variance(dataset: Dataset, fold_id: np.ndarray, k: int) -> dict:
    e_raw, m0, m1 = ref_oof_arrays(dataset, fold_id, k)
    e = np.clip(e_raw, 0.01, 0.99)
    n = dataset.n
    phi = np.array([
        m1[i] - m0[i]
        + dataset.a[i] * (dataset.y[i] - m1[i]) / e[i]
        - (1.0 - dataset.a[i]) * (dataset.y[i] - m0[i]) / (1.0 - e[i])
        for i in range(n)
    ])
    point = float(phi.mean())
    groups = np.unique(dataset.clusters)
    c_sums = np.array([phi[dataset.clusters == g].sum() for g in groups])
    g = groups.size
    # independent units are clusters: V = G/(n^2 (G-1)) sum (C_g - Cbar)^2
    var = float(g * np.sum((c_sums - c_sums.mean()) ** 2) / (n ** 2 * (g - 1)))
    return {"point": point, "cluster_se": float(np.sqrt(var)),
            "n_clusters": int(g)}
