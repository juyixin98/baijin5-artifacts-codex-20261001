"""Estimation kernels: g-computation, IPW and the cross-fitted AIPW estimator.

Notation
--------
``e``  = out-of-fold propensity P_hat(A=1 | X), trimmed before weighting
``m0`` = out-of-fold outcome regression for A=0
``m1`` = out-of-fold outcome regression for A=1

Unstabilized (Horvitz-Thompson) ATE influence-function score per row::

    phi_i = m1_i - m0_i
            + A_i (Y_i - m1_i) / e_i
            - (1 - A_i) (Y_i - m0_i) / (1 - e_i)

with ``point = mean(phi_i)``.

Stabilized weights (when configured) use the per-arm augmented HAJEK
estimator. With stabilized weights w1 = p1/e, w0 = p0/(1-e)::

    rho1 = sum(w1 A (Y - m1)) / sum(w1 A)          # normalized residual corr.
    rho0 = sum(w0 (1-A) (Y - m0)) / sum(w0 (1-A))
    mu1  = mean(m1) + rho1
    mu0  = mean(m0) + rho0

This is DR: if m is correct the residual term has conditional mean zero for
any e; if e is correct rho1 -> E[mu1(X) - m1(X)] and mu1 -> E[Y(1)].
Its per-arm influence contributions are::

    s_i = A (Y-m1)/e,  d_i = A/e
    phi1_i = (m1_i - mean(m1)) + (s_i - rho1 d_i) / mean(d_i)
    phi0_i = (m0_i - mean(m0)) + (t_i - rho0 u_i) / mean(u_i)
    phi_i  = phi1_i - phi0_i  (mean zero by construction)
    stored score_i = point + phi_i  so that mean(score_i) == point holds

The centered contributions phi_i are mean zero by construction; the stored
per-row score is shifted by the point so the package-wide invariant
``point == mean(scores)`` and the variance code's ``scores - point`` centering
apply unchanged. The IPW comparator under stabilization is the Hájek IPW
contrast
``sum(d1 Y)/sum(d1) - sum(d0 Y)/sum(d0)``.

ATT follows Abadie-Imbens style augmentation::

    num_i = A (m1-m0) + A (Y-m1) - (1-A) e/(1-e) (Y-m0)
    point = sum(num_i) / sum(A)

returned on the mean-score convention ``phi_i = num_i * n / sum(A)`` so that
``point = mean(phi_i)`` and the clustered variance formula applies unchanged.

Trimming clips ``e`` to ``[lower, upper]`` BEFORE weighting and reports the
fraction clipped. Trimming changes the effective estimand toward an overlap
population; it is never applied silently.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contract import ComputationFailure, Estimand


@dataclass(frozen=True)
class Components:
    point: float
    gcomp: float
    ipw: float
    trimmed_fraction: float
    e_trimmed: np.ndarray
    scores: np.ndarray
    """Centered-or-raw influence contributions; variance code centers itself."""


def trim_propensity(e: np.ndarray, lower: float,
                    upper: float) -> tuple[np.ndarray, float]:
    e = np.asarray(e, dtype=float)
    clipped = np.clip(e, lower, upper)
    return clipped, float(np.mean((e < lower) | (e > upper)))


def gcomp_point(mu0: np.ndarray, mu1: np.ndarray) -> float:
    return float(np.mean(mu1 - mu0))


def ipw_ate_ht(a: np.ndarray, y: np.ndarray, e: np.ndarray) -> float:
    return float(np.mean(a * y / e - (1.0 - a) * y / (1.0 - e)))


def _ate_ht(a: np.ndarray, y: np.ndarray, e: np.ndarray,
            mu0: np.ndarray, mu1: np.ndarray) -> tuple[float, np.ndarray, float]:
    scores = (mu1 - mu0
              + a * (y - mu1) / e
              - (1.0 - a) * (y - mu0) / (1.0 - e))
    point = float(np.mean(scores))
    return point, scores, ipw_ate_ht(a, y, e)


def _ate_hajek(a: np.ndarray, y: np.ndarray, e: np.ndarray,
               mu0: np.ndarray, mu1: np.ndarray) -> tuple[float, np.ndarray, float]:
    """Augmented per-arm Hájek estimator with its influence scores."""
    a = np.asarray(a, dtype=float)
    y = np.asarray(y, dtype=float)

    # ---- treated arm ----
    s1 = a * (y - mu1) / e
    d1 = a / e
    sum_d1 = float(np.sum(d1))
    if sum_d1 <= 0.0:
        raise ComputationFailure("treated-arm Hajek denominator is non-positive")
    rho1 = float(np.sum(s1) / sum_d1)
    mean_m1 = float(np.mean(mu1))
    mean_d1 = sum_d1 / a.shape[0]
    phi1 = (mu1 - mean_m1) + (s1 - rho1 * d1) / mean_d1
    arm1 = mean_m1 + rho1

    # ---- control arm ----
    s0 = (1.0 - a) * (y - mu0) / (1.0 - e)
    d0 = (1.0 - a) / (1.0 - e)
    sum_d0 = float(np.sum(d0))
    if sum_d0 <= 0.0:
        raise ComputationFailure("control-arm Hajek denominator is non-positive")
    rho0 = float(np.sum(s0) / sum_d0)
    mean_m0 = float(np.mean(mu0))
    mean_d0 = sum_d0 / a.shape[0]
    phi0 = (mu0 - mean_m0) + (s0 - rho0 * d0) / mean_d0
    arm0 = mean_m0 + rho0

    scores = (arm1 + phi1) - (arm0 + phi0)  # mean(scores) == arm1-arm0 == point
    point = arm1 - arm0

    # plain (non-augmented) stabilized Hajek IPW comparator
    ipw1 = float(np.sum(d1 * y) / sum_d1)
    ipw0 = float(np.sum(d0 * y) / sum_d0)
    return point, scores, ipw1 - ipw0


def _att(a: np.ndarray, y: np.ndarray, e: np.ndarray,
         mu0: np.ndarray, mu1: np.ndarray) -> tuple[float, np.ndarray]:
    n = a.shape[0]
    numer = (a * (mu1 - mu0)
             + a * (y - mu1)
             - (1.0 - a) * (e / (1.0 - e)) * (y - mu0))
    n_treated = float(np.sum(a))
    if n_treated == 0.0:
        raise ComputationFailure("ATT undefined: no treated units")
    point = float(np.sum(numer) / n_treated)
    scores = numer * (n / n_treated)  # mean(scores) == point
    return point, scores


def estimate(a, y, e_raw, mu0, mu1, trim_cfg, estimand, stabilized) -> Components:
    """Assemble estimator components for one run; PS trimming applied first."""
    if trim_cfg.enabled:
        e, frac = trim_propensity(e_raw, trim_cfg.lower, trim_cfg.upper)
    else:
        e = np.asarray(e_raw, dtype=float)
        frac = 0.0
        if np.any((e <= 0.0) | (e >= 1.0)):
            raise ComputationFailure("extreme propensity with trimming disabled")

    a = np.asarray(a, dtype=float)
    y = np.asarray(y, dtype=float)
    gc = gcomp_point(mu0, mu1)

    if estimand is Estimand.ATT:
        point, scores = _att(a, y, e, mu0, mu1)
        comp = Components(point=point, gcomp=gc, ipw=float("nan"),
                          trimmed_fraction=frac, e_trimmed=e, scores=scores)
    elif stabilized:
        point, scores, ipw = _ate_hajek(a, y, e, mu0, mu1)
        comp = Components(point=point, gcomp=gc, ipw=ipw,
                          trimmed_fraction=frac, e_trimmed=e, scores=scores)
    else:
        point, scores, ipw = _ate_ht(a, y, e, mu0, mu1)
        comp = Components(point=point, gcomp=gc, ipw=ipw,
                          trimmed_fraction=frac, e_trimmed=e, scores=scores)

    if not np.isfinite(comp.point) or np.any(~np.isfinite(comp.scores)):
        raise ComputationFailure("non-finite AIPW estimate or influence scores")
    return comp
