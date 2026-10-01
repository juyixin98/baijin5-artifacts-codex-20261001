"""Independent numerical reference implementations.

The validation suite is required to compare the core estimator against a
reference that was **not** produced by the code under test. This module
deliberately re-derives the local-linear fit through three *different*
numerical paths and shares no code with :mod:`app.estimator` /
:mod:`app.inference`:

* :func:`explicit_side_intercept` - closed-form weighted simple regression
  via raw weighted sums solved with ``scipy.linalg.solve`` (not lstsq);
* :func:`optimized_side_intercept` - numerical minimisation of the WLS
  objective with ``scipy.optimize.minimize`` (BFGS), a derivative-free
  cross-check of the linear algebra;
* :func:`wild_bootstrap_t` - a textbook bootstrap-*t* loop that re-estimates
  the standard error on every draw (the core ships the cheaper fixed-SE
  variant), giving an independently derived rejection rate to compare.

The known-truth DGPs in :mod:`app.datasets` are the third, statistical
reference: point estimates are compared to the DGP's true jump, not to the
core's own output.
"""
from __future__ import annotations

import numpy as np
from scipy import linalg, optimize, stats


def _kernel_values(u: np.ndarray, kernel: str) -> np.ndarray:
    au = np.abs(u)
    if kernel == "uniform":
        return (au <= 1.0).astype(float)
    if kernel == "epanechnikov":
        return np.where(au <= 1.0, 0.75 * (1.0 - au**2), 0.0)
    return np.where(au <= 1.0, 1.0 - au, 0.0)  # triangular default


def _gather(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    h: float,
    kernel: str,
    side: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mask = x >= cutoff if side == "right" else x <= cutoff
    xs, ys = x[mask], y[mask]
    d = xs - cutoff
    w = _kernel_values(d / h, kernel)
    keep = w > 0
    return d[keep], ys[keep], w[keep]


def explicit_side_intercept(
    x: np.ndarray, y: np.ndarray, cutoff: float, h: float, kernel: str, side: str
) -> float:
    """Intercept at the cutoff from raw 2x2 weighted normal equations."""
    d, yv, w = _gather(x, y, cutoff, h, kernel, side)
    s0 = np.sum(w)
    s1 = np.sum(w * d)
    s2 = np.sum(w * d * d)
    t0 = np.sum(w * yv)
    t1 = np.sum(w * d * yv)
    A = np.array([[s0, s1], [s1, s2]], dtype=float)
    b = np.array([t0, t1], dtype=float)
    beta = linalg.solve(A, b, assume_a="pos")
    return float(beta[0])


def explicit_jump(
    x: np.ndarray, y: np.ndarray, cutoff: float, h: float, kernel: str = "triangular"
) -> float:
    a_l = explicit_side_intercept(x, y, cutoff, h, kernel, "left")
    a_r = explicit_side_intercept(x, y, cutoff, h, kernel, "right")
    return float(a_r - a_l)


def optimized_side_intercept(
    x: np.ndarray, y: np.ndarray, cutoff: float, h: float, kernel: str, side: str
) -> float:
    """WLS intercept found by numerically minimising the weighted SSR."""
    d, yv, w = _gather(x, y, cutoff, h, kernel, side)

    def objective(beta: np.ndarray) -> float:
        resid = yv - beta[0] - beta[1] * d
        return float(np.sum(w * resid**2))

    res = optimize.minimize(
        objective,
        x0=np.array([0.0, 0.0]),
        method="BFGS",
        options={"gtol": 1e-12, "maxiter": 1000},
    )
    if not res.success:  # pragma: no cover - numerical guard
        raise RuntimeError(f"reference optimisation failed: {res.message}")
    return float(res.x[0])


def _explicit_se(d: np.ndarray, yv: np.ndarray, w: np.ndarray) -> float:
    """HC3 standard error of the intercept, built from raw sums (independent)."""
    s0 = np.sum(w)
    s1 = np.sum(w * d)
    s2 = np.sum(w * d * d)
    A = np.array([[s0, s1], [s1, s2]])
    Ainv = linalg.solve(A, np.eye(2), assume_a="pos")
    beta = Ainv @ np.array([np.sum(w * yv), np.sum(w * d * yv)])
    resid = yv - beta[0] - beta[1] * d
    X = np.column_stack([np.ones_like(d), d])
    # leverage h_i = w_i x_i' A^-1 x_i
    hat = w * np.einsum("ij,jk,ik->i", X, Ainv, X)
    r2 = resid**2 / (1.0 - np.clip(hat, -1.0, 0.999999)) ** 2
    meat = (X * (w * np.sqrt(r2))[:, None]).T @ (X * (w * np.sqrt(r2))[:, None])
    vcov = Ainv @ meat @ Ainv
    return float(np.sqrt(max(vcov[0, 0], 0.0)))


def wild_bootstrap_t(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    h: float,
    kernel: str,
    reps: int,
    seed: int,
) -> dict:
    """Independent reference: restricted-null wild bootstrap-t.

    Unlike the core's fixed-SE bootstrap p-value, here the SE is recomputed
    for every synthetic sample (the canonical bootstrap-t), so agreement in
    rejection behaviour validates both the core estimator and its SE.
    """
    dl, yl, wl = _gather(x, y, cutoff, h, kernel, "left")
    dr, yr, wr = _gather(x, y, cutoff, h, kernel, "right")

    def fit(d, yv, w):
        A = np.array(
            [[np.sum(w), np.sum(w * d)], [np.sum(w * d), np.sum(w * d * d)]]
        )
        b = linalg.solve(
            A, np.array([np.sum(w * yv), np.sum(w * d * yv)]), assume_a="pos"
        )
        return b, yv - b[0] - b[1] * d

    bl, rl = fit(dl, yl, wl)
    br, rr = fit(dr, yr, wr)
    tau_obs = br[0] - bl[0]

    def se_of(d, yv, w) -> float:
        return _explicit_se(d, yv, w)

    se_obs = np.sqrt(se_of(dl, yl, wl) ** 2 + se_of(dr, yr, wr) ** 2)
    t_obs = tau_obs / se_obs

    rng = np.random.default_rng(seed)
    exceed = 0
    t_stars: list[float] = []
    # Impose the null on the right-side conditional mean.
    yr_null_mean = br[0] - tau_obs + br[1] * dr
    for _ in range(reps):
        vl = rng.choice([-1.0, 1.0], size=dl.size)
        vr = rng.choice([-1.0, 1.0], size=dr.size)
        y_l = bl[0] + bl[1] * dl + vl * rl
        y_r = yr_null_mean + vr * rr
        bl_s, _ = fit(dl, y_l, wl)
        br_s, _ = fit(dr, y_r, wr)
        tau_s = br_s[0] - bl_s[0]
        se_s = np.sqrt(se_of(dl, y_l, wl) ** 2 + se_of(dr, y_r, wr) ** 2)
        t_s = tau_s / se_s if se_s > 0 else 0.0
        t_stars.append(t_s)
        if abs(t_s) >= abs(t_obs):
            exceed += 1
    t_arr = np.asarray(t_stars)
    # KS check that the null bootstrap-t distribution is approximately the
    # standard-normal reference the analytic z-test assumes.
    ks = stats.kstest(t_arr, "norm")
    q95 = float(np.quantile(np.abs(t_arr), 0.95))
    return {
        "tau_obs": float(tau_obs),
        "se_obs": float(se_obs),
        "p_value": float(max(exceed / reps, 1.0 / reps)),
        "ci_low": float(tau_obs - q95 * se_obs),
        "ci_high": float(tau_obs + q95 * se_obs),
        "null_t_std": float(np.std(t_arr)),
        "null_t_ks_stat": float(ks.statistic),
        "null_t_ks_pvalue": float(ks.pvalue),
    }
