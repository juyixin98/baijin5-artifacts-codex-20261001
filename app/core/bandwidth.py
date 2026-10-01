"""Bandwidth selection for local-linear RD.

Two reproducible rules are provided:

``rot``
    Per-side Silverman/Fan-Gijbels rule-of-thumb::

        h = 0.9 * min(std(x), IQR(x)/1.34) * n^{-1/5}

    clipped to the available support on that side.  Deterministic, no
    optimization — used as the pilot and as a robust fallback.

``ik``
    An Imbens–Kalyanaraman (2012) *style* asymptotic plugin rule:

    1. fit weighted local quadratics on each side with a pilot bandwidth to
       estimate second derivatives ``m''_-``, ``m''_+`` and residual variance;
    2. estimate the running-variable density ``f(c)`` by a one-sided KDE;
    3. form ``h = C_K [sigma2 / (f(c) (m''_- + m''_+)^2)]^{1/5} n^{-1/5}``
       with kernel constants from IK (2012, Table 2).

    When curvature is (numerically) zero the MSE formula is undefined; we then
    fall back to ROT *and record the fallback in the result diagnostics* —
    never silently substitute an arbitrary number.

This is a documented, self-contained implementation, not a wrapper around a
specific R package; numerical agreement with ``rdrobust`` is approximate by
design (their regularization steps differ).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np
from numpy.typing import NDArray

from .kernels import KERNELS

Array = NDArray[np.float64]

# IK (2012), Table 2 kernel constants for their regularization-free formula.
IK_KERNEL_CONSTANTS: Dict[str, float] = {
    "uniform": 1.843,
    "triangular": 3.4375,
    "epanechnikov": 3.1999,
    "tricube": 3.15,  # close to Epanechnikov; documented interpolation
}

ROT_SCALE = 0.9
CURVATURE_FLOOR = 1e-8
DENSITY_FLOOR = 1e-12


@dataclass(frozen=True)
class BandwidthChoice:
    method: str
    h_left: float
    h_right: float
    details: Dict[str, float] = field(default_factory=dict)
    fell_back: bool = False
    fallback_reason: str | None = None


def _rot_side(dist: Array) -> float:
    """Rule-of-thumb bandwidth for one side (distances from cutoff)."""
    n = dist.size
    if n < 2:
        return float("nan")
    std = float(np.std(dist, ddof=1))
    q75, q25 = np.percentile(dist, [75, 25])
    iqr_scale = float((q75 - q25) / 1.34)
    spread = min(std, iqr_scale) if iqr_scale > 0 else std
    if not np.isfinite(spread) or spread <= 0.0:
        spread = float(np.max(dist))
    h = ROT_SCALE * spread * n ** (-0.2)
    support = float(np.max(dist))
    if not np.isfinite(h) or h <= 0.0:
        return support
    return float(min(h, support))


def rule_of_thumb(x: Array, cutoff: float) -> tuple[float, float]:
    """Per-side ROT bandwidths."""
    left = cutoff - x[x < cutoff]
    right = x[x > cutoff] - cutoff
    return _rot_side(left), _rot_side(right)


def _weighted_design(dist: Array, y: Array, w: Array, order: int) -> Array:
    """Weighted polynomial regression coefficients (low to high power)."""
    n = dist.size
    X = np.column_stack([dist**j for j in range(order + 1)])
    Xw = X * np.sqrt(w)[:, None]
    beta, _, rank, _ = np.linalg.lstsq(Xw, y * np.sqrt(w), rcond=None)
    if rank < order + 1:
        raise np.linalg.LinAlgError(f"pilot rank {rank} < {order + 1}")
    return beta


def _quadratic_pilot(
    dist: Array, y: Array, h_pilot: float, kernel_name: str
) -> tuple[float, float]:
    """Return (second derivative m'', residual variance) at the boundary."""
    kernel = KERNELS[kernel_name]
    inside = (dist > 0.0) & (dist <= h_pilot)
    d, v = dist[inside], y[inside]
    if d.size < 6:
        raise np.linalg.LinAlgError(f"pilot only has {d.size} points")
    w = kernel(d / h_pilot)
    beta = _weighted_design(d, v, w, order=2)
    resid = v - np.column_stack([d**j for j in range(3)]) @ beta
    dof = max(d.size - 3, 1)
    sigma2 = float(w @ resid**2 / (np.sum(w) * dof / d.size))
    return float(2.0 * beta[2]), sigma2


def _boundary_density(dist: Array, h: float, kernel_name: str) -> float:
    """One-sided KDE of the running variable at the cutoff, f_s(c)."""
    kernel = KERNELS[kernel_name]
    n = dist.size
    inside = (dist > 0.0) & (dist <= h)
    if n == 0 or not np.any(inside):
        return 0.0
    # One-sided density: normalise with factor 2 because support is [0, h]
    # rather than [-h, h].
    return float(2.0 * np.sum(kernel(dist[inside] / h)) / (n * h))


def plugin_bandwidth(
    x: Array, y: Array, cutoff: float, kernel_name: str = "triangular"
) -> BandwidthChoice:
    """IK-style plugin bandwidth, shared across both sides as in IK.

    A single ``h`` is returned for both sides (the IK convention); the
    estimator still fits the two sides separately.
    """
    xv, yv = np.asarray(x, float), np.asarray(y, float)
    left_dist, left_y = cutoff - xv[xv < cutoff], yv[xv < cutoff]
    right_dist, right_y = xv[xv > cutoff] - cutoff, yv[xv > cutoff]
    h_l_rot, h_r_rot = _rot_side(left_dist), _rot_side(right_dist)
    details: Dict[str, float] = {
        "pilot_h_left": h_l_rot,
        "pilot_h_right": h_r_rot,
    }

    try:
        m2_l, sig2_l = _quadratic_pilot(left_dist, left_y, h_l_rot, kernel_name)
        m2_r, sig2_r = _quadratic_pilot(right_dist, right_y, h_r_rot, kernel_name)
    except np.linalg.LinAlgError as exc:
        h = float(np.nanmean([h_l_rot, h_r_rot]))
        return BandwidthChoice("ik", h, h, details, True, f"pilot failed: {exc}")

    f_l = _boundary_density(left_dist, h_l_rot, kernel_name)
    f_r = _boundary_density(right_dist, h_r_rot, kernel_name)
    f_c = 0.5 * (f_l + f_r)
    sigma2 = 0.5 * (sig2_l + sig2_r)
    curvature = (m2_l + m2_r) ** 2
    details.update(
        m2_left=m2_l, m2_right=m2_r, sigma2=sigma2,
        density_left=f_l, density_right=f_r, density_cutoff=f_c,
    )

    n_eff = left_dist.size + right_dist.size
    if curvature < CURVATURE_FLOOR or f_c < DENSITY_FLOOR or not np.isfinite(curvature):
        h = float(np.nanmean([h_l_rot, h_r_rot]))
        return BandwidthChoice(
            "ik", h, h, details, True,
            "vanishing curvature or density; MSE plugin undefined, ROT used",
        )

    ck = IK_KERNEL_CONSTANTS[kernel_name]
    h = ck * (sigma2 / (f_c * curvature)) ** 0.2 * n_eff ** (-0.2)
    support = float(min(np.max(left_dist), np.max(right_dist)))
    if not np.isfinite(h) or h <= 0.0:
        return BandwidthChoice(
            "ik", float(np.nanmean([h_l_rot, h_r_rot])),
            float(np.nanmean([h_l_rot, h_r_rot])), details, True,
            "non-finite plugin value, ROT used",
        )
    h = float(min(h, support))
    return BandwidthChoice("ik", h, h, details, False, None)


def select_bandwidth(
    x: Array, y: Array, cutoff: float, method: str, kernel_name: str
) -> BandwidthChoice:
    """Dispatch on method name; unknown methods fail loudly."""
    key = method.strip().lower()
    if key == "rot":
        hl, hr = rule_of_thumb(x, cutoff)
        return BandwidthChoice("rot", hl, hr, {}, False, None)
    if key == "ik":
        return plugin_bandwidth(x, y, cutoff, kernel_name)
    raise ValueError(f"unknown bandwidth method {method!r}; use 'rot', 'ik' or a number")
