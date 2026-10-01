"""Bandwidth selection.

Two reproducible choices are supported:

* ``manual``: the caller pins a single positive bandwidth (used for both
  sides; the two fits remain independent).
* ``ik_rot``: a deterministic Imbens--Kalyanaraman (2012) style rule of
  thumb for the *common* bandwidth of a sharp-RD local-linear estimator.

The IK ROT chooses h by balancing squared bias (driven by the difference of
the side curvatures) against variance, with the IK regularisation term that
keeps the selector finite when the estimated curvature difference vanishes
(e.g. the no-jump linear null). It returns a *common* bandwidth applied to
both sides, as in the original paper; the two regressions are still fit
separately. The selected value is clamped to the observed support and to a
window guaranteed to contain enough neighbours for a line, so a degenerate
ROT can never produce an empty fit.

Simplifications versus full IK (deliberate, documented):

* the pilot is a weighted local quadratic in a Silverman-style pilot window
  rather than IK's full initial-bandwidth iteration;
* the universal IK kernel constant ``C_K = 3.4375`` is used without a
  per-kernel moment re-derivation. Use ``bandwidth_multiplier`` and the
  manual method for formal bandwidth sensitivity analysis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.contract import BandwidthMethod, KernelName
from app.errors import BandwidthFailedError, InsufficientDataError

IK_C_K = 3.4375
IK_REG_FACTOR = 2160.0  # IK regularisation numerator for the edge kernel


@dataclass(frozen=True)
class BandwidthSelection:
    left: float
    right: float
    method: BandwidthMethod
    notes: str


def _side_arrays(
    x: np.ndarray, y: np.ndarray, cutoff: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    left = x < cutoff
    right = x > cutoff
    at = x == cutoff
    # Points exactly at c are excluded from curvature pilots (mass-at-cutoff
    # is a separate diagnostic); they never carry weight in ROT selection.
    return x[left], y[left], x[right], y[right]


def _robust_scale(d: np.ndarray) -> float:
    if d.size < 2:
        return 1.0
    iqr = np.subtract(*np.percentile(d, [75, 25]))
    s = iqr / 1.349 if iqr > 0 else np.std(d)
    return float(s if s > 0 else 1.0)


def _pilot_window(dist: np.ndarray) -> float:
    """Silverman-style pilot radius on a side (distances > 0)."""
    n = dist.size
    scale = _robust_scale(dist)
    h = 1.06 * scale * n ** (-1.0 / 5.0)
    h = max(h, float(np.sort(dist)[min(n - 1, max(2, n // 5))]))
    return min(h, float(dist.max()))


def _pilot_quantities(dist: np.ndarray, y: np.ndarray, h_pilot: float) -> tuple[float, float, float]:
    """Return (second derivative, residual variance, density at boundary).

    Local quadratic WLS with a triangular pilot kernel.
    """
    m = dist <= h_pilot
    d, yy = dist[m], y[m]
    if d.size < 3:
        raise BandwidthFailedError(
            "pilot window contains fewer than 3 points on a side",
            {"n_pilot": int(d.size)},
        )
    k = np.clip(1.0 - d / h_pilot, 0.0, 1.0)
    X = np.column_stack([np.ones_like(d), d, d * d])
    sw = np.sqrt(k)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], yy * sw, rcond=None)
    resid = yy - X @ beta
    # Weighted residual variance with a degrees-of-freedom correction for the
    # three pilot coefficients. Boundary density follows from the local mass:
    # mass in [0, h] ~= f(c) * h, hence f = count_pilot / (n_side * h).
    n_pilot = d.size
    sigma2 = float(np.sum(k * resid**2) / np.sum(k)) * n_pilot / max(1, n_pilot - 3)
    density = float(n_pilot / (dist.size * h_pilot))
    curvature2 = 2.0 * float(beta[2])
    return curvature2, sigma2, max(density, 1e-12)


def _kth_distance(dist: np.ndarray, k: int) -> float:
    k = max(1, min(k, dist.size))
    return float(np.sort(dist)[k - 1])


def select_bandwidth(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    method: BandwidthMethod,
    manual: float | None,
    multiplier: float,
    min_obs: int,
    kernel: KernelName,  # noqa: ARG001 - documented: IK constant is kernel-agnostic here
) -> BandwidthSelection:
    if method is BandwidthMethod.MANUAL:
        if manual is None or not np.isfinite(manual) or manual <= 0:
            raise BandwidthFailedError(
                "bandwidth_method=manual requires a positive bandwidth"
            )
        h = float(manual) * float(multiplier)
        return BandwidthSelection(
            left=h, right=h, method=method, notes="manual common bandwidth"
        )

    xl, yl, xr, yr = _side_arrays(x, y, cutoff)
    if xl.size < 3 or xr.size < 3:
        raise InsufficientDataError(
            "need at least 3 observations strictly on each side for an IK pilot",
            {"n_left": int(xl.size), "n_right": int(xr.size)},
        )

    dl, dr = cutoff - xl, xr - cutoff
    hl = _pilot_window(dl)
    hr = _pilot_window(dr)
    m2_l, s2_l, f_l = _pilot_quantities(dl, yl, hl)
    m2_r, s2_r, f_r = _pilot_quantities(dr, yr, hr)

    n = x.size
    sigma2 = 0.5 * (s2_l + s2_r)
    density = 0.5 * (f_l + f_r)
    curvature_gap = m2_r - m2_l

    # IK regularisation: prevents the denominator collapsing to zero on the
    # linear null where the curvature difference is estimated as ~ 0.
    reg = IK_REG_FACTOR * 0.5 * (
        sigma2 / (xl.size**2 * hl**5) + sigma2 / (xr.size**2 * hr**5)
    )
    denom = max(curvature_gap**2 + reg, 1e-12)
    h_ik = IK_C_K * (2.0 * sigma2 / (density * denom)) ** 0.2 * n ** (-0.2)
    if not math.isfinite(h_ik) or h_ik <= 0:
        raise BandwidthFailedError(f"IK ROT produced non-finite bandwidth: {h_ik}")

    # Clamp to support and to a window with enough neighbours for a line.
    floor_h = max(_kth_distance(dl, max(3, min_obs)), _kth_distance(dr, max(3, min_obs)))
    ceil_h = min(float(dl.max()), float(dr.max()))
    if floor_h > ceil_h:  # one side simply cannot supply min_obs points
        floor_h = max(_kth_distance(dl, 3), _kth_distance(dr, 3))
    h = float(np.clip(h_ik, floor_h, ceil_h)) * float(multiplier)
    h = min(h, max(float(dl.max()), float(dr.max())))

    notes = (
        f"IK-style ROT: h_raw={h_ik:.6g}, curvature_gap={curvature_gap:.4g}, "
        f"sigma2={sigma2:.4g}, density={density:.4g}, regularisation={reg:.4g}; "
        "common bandwidth, separate side fits"
    )
    return BandwidthSelection(left=h, right=h, method=method, notes=notes)
