"""Independent reference implementation used ONLY by the tests.

The test oracle is deliberately written from scratch and never imports the
estimator under test (``app.core.estimator`` / ``wls``). Point estimates are
computed three different ways:

1. ``wls_normal_equations`` - closed-form weighted least squares assembled and
   solved explicitly with ``numpy.linalg.solve`` (the production code uses
   ``lstsq`` on the square-root-weighted design).
2. ``numpy.polyfit``        - NumPy's independent weighted polynomial fitter
   (weights enter as 1/sigma, hence sqrt(w)).
3. ``scipy.stats.linregress`` - ordinary least squares, applicable for the
   uniform-kernel special case where every in-window weight equals one.

Standard errors here use the plain HC0/HC1 sandwich rather than the
production HC2 formula, so the cross-check on SEs is between independently
derived robust estimators (expected to be close, not bit-identical), while
the cross-check on coefficients is exact to numerical tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class RefWLS:
    intercept: float
    slope: float
    se_hc0: float
    se_hc1: float
    se_const: float
    n: int


def wls_normal_equations(d: Array, y: Array, w: Array) -> RefWLS:
    """Weighted linear regression via explicit normal equations.

    Minimises sum_i w_i (y_i - a - b d_i)^2; assembled directly instead of
    reusing the production WLS routine.
    """
    d = np.asarray(d, float)
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    n = d.size
    sw = float(np.sum(w))
    swd = float(np.sum(w * d))
    swd2 = float(np.sum(w * d * d))
    swy = float(np.sum(w * y))
    swdy = float(np.sum(w * d * y))
    xtwx = np.array([[sw, swd], [swd, swd2]], dtype=float)
    xtwy = np.array([swy, swdy], dtype=float)
    beta = np.linalg.solve(xtwx, xtwy)
    inv = np.linalg.inv(xtwx)

    resid = y - beta[0] - beta[1] * d
    # Homoskedastic sigma^2 estimate
    dof = max(n - 2, 1)
    sigma2 = float(np.sum(w * resid**2) / dof)
    var_const = sigma2 * inv

    # HC0 robust: (X'WX)^-1 X' W diag(e^2) W X (X'WX)^-1
    x1 = np.column_stack([np.ones(n), d])
    xtw = x1.T * w[None, :]
    meat = (xtw * (resid**2)[None, :]) @ xtw.T
    var_hc0 = inv @ meat @ inv
    var_hc1 = var_hc0 * (n / dof)
    return RefWLS(
        intercept=float(beta[0]),
        slope=float(beta[1]),
        se_hc0=float(np.sqrt(max(var_hc0[0, 0], 0.0))),
        se_hc1=float(np.sqrt(max(var_hc1[0, 0], 0.0))),
        se_const=float(np.sqrt(max(var_const[0, 0], 0.0))),
        n=n,
    )


def _kernel_weights(name: str, u: Array) -> Array:
    au = np.abs(u)
    if name == "triangular":
        return np.where(au <= 1.0, 1.0 - au, 0.0)
    if name == "epanechnikov":
        return np.where(au <= 1.0, 0.75 * (1.0 - au**2), 0.0)
    if name == "uniform":
        return np.where(au <= 1.0, 1.0, 0.0)
    if name == "tricube":
        return np.where(au <= 1.0, (1.0 - au**3) ** 3, 0.0)
    raise ValueError(name)


@dataclass(frozen=True)
class RefRD:
    tau: float
    intercept_left: float
    intercept_right: float
    se_left: float
    se_right: float
    se_tau: float
    n_left: int
    n_right: int


def rd_reference(
    x: Array, y: Array, *, cutoff: float = 0.0,
    h: float | tuple[float, float] = 0.2, kernel: str = "triangular",
) -> RefRD:
    """Independent sharp-RD point estimate + HC1 SEs from normal equations."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    hl = hr = h if np.isscalar(h) else h  # type: ignore[assignment]

    def side(mask: bool, hside: float) -> RefWLS:
        d = np.abs((x[mask] - cutoff))
        v = y[mask]
        w = _kernel_weights(kernel, d / hside)
        keep = w > 0.0
        return wls_normal_equations(d[keep], v[keep], w[keep])

    left = side(x < cutoff, hl)
    right = side(x > cutoff, hr)
    se_tau = float(np.hypot(left.se_hc1, right.se_hc1))
    return RefRD(
        tau=right.intercept - left.intercept,
        intercept_left=left.intercept,
        intercept_right=right.intercept,
        se_left=left.se_hc1,
        se_right=right.se_hc1,
        se_tau=se_tau,
        n_left=left.n,
        n_right=right.n,
    )


def polyfit_boundary_intercept(d: Array, y: Array, w: Array) -> float:
    """Boundary intercept via NumPy's independent ``polyfit`` code path."""
    # np.polyfit weights are 1/sigma; pass sqrt(w) so that squared = w.
    coeff = np.polyfit(np.asarray(d), np.asarray(y), 1,
                       w=np.sqrt(np.asarray(w)))
    return float(coeff[1])  # polynomial evaluated at d=0 is the constant term


def linregress_uniform_boundary(d: Array, y: Array) -> float:
    """OLS boundary intercept (uniform kernel, all-equal weights)."""
    res = stats.linregress(np.asarray(d), np.asarray(y))
    return float(res.intercept)
