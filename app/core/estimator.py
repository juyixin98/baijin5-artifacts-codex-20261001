"""Side-specific local-linear RD estimator — the statistical contract.

Given observations ``(x_i, y_i)`` and a cutoff ``c``:

1. observations exactly at the cutoff are excluded (ambiguous treatment);
2. each side is fit **separately** by weighted local linear regression with a
   compact-support kernel and a per-side bandwidth — the jump is

       tau_hat = mu_+(c) - mu_-(c),

   i.e. the difference of the two fitted boundary intercepts.  It is **never**
   a difference of raw side means;
3. the variance of the jump combines the two independent side variances with
   the chosen sandwich type (default HC2).  A bias-aware discussion is
   documented in the README and surfaced via a smoothing-bias bound diagnostic.

The estimator never raises on "soft" problems: it returns a structured
:class:`~app.core.contracts.RDResult` with ``status`` success / warning / failed
and an explicit :class:`FailureCategory`.
"""
from __future__ import annotations

import json
import platform
import uuid
from typing import Any, Dict

import numpy as np
from scipy import stats
from numpy.typing import NDArray

from .. import __version__
from .bandwidth import select_bandwidth
from .contracts import (
    FailureCategory,
    FitError,
    RDResult,
    RunStatus,
    SideFit,
)
from .diagnostics import run_all_diagnostics
from .kernels import get_kernel
from .wls import weighted_least_squares

Array = NDArray[np.float64]

SUPPORTED_SE_TYPES = ("const", "hc1", "hc2")
# Minimum positive-weight observations per side to attempt a fit.
MIN_SIDE_OBS = 3
# Distinct running-variable support points desired inside a bandwidth.
MIN_DISTINCT_SUPPORT = 10
MIN_DISTINCT_HARD = 3
# Extrapolation factor = gap to closest observation / span of data actually
# inside the window. >= 1 means reaching the cutoff requires extrapolating a
# distance larger than the whole observed local span: not nonparametrically
# identified. In [EXT_WARN_FACTOR, 1) we still estimate but flag it.
EXT_FAIL_FACTOR = 1.0
EXT_WARN_FACTOR = 0.25


def _versions() -> Dict[str, str]:
    return {
        "app": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": __import__("scipy").__version__,
    }


def _fit_side(
    dist: Array, y: Array, h: float, kernel_name: str, side: str
) -> SideFit:
    """Local-linear fit on one side; ``dist`` is distance from the cutoff."""
    kernel = get_kernel(kernel_name)
    inside = (dist > 0.0) & (dist <= h)
    d, v = dist[inside], y[inside]
    if d.size < MIN_SIDE_OBS:
        raise FitError(
            f"{side} side: {d.size} obs within bandwidth {h:.6g} "
            f"(need >= {MIN_SIDE_OBS})"
        )
    w = kernel(d / h)
    if np.count_nonzero(w > 0) < MIN_SIDE_OBS:
        raise FitError(f"{side} side: fewer than {MIN_SIDE_OBS} positive-weight obs")
    res = weighted_least_squares(d, v, w)
    return SideFit(
        side=side,  # type: ignore[arg-type]
        intercept=res.intercept,
        slope=res.slope,
        bandwidth=float(h),
        n=res.n,
        sum_weights=res.sum_weights,
        se_homoskedastic=res.se_homoskedastic,
        se_hc1=res.se_hc1,
        se_hc2=res.se_hc2,
        sse=res.sse,
        support_min=float(d.min()),
        support_max=float(d.max()),
    )


def _se_of(side: SideFit, se_type: str) -> float:
    return {
        "const": side.se_homoskedastic,
        "hc1": side.se_hc1,
        "hc2": side.se_hc2,
    }[se_type]


def _bias_bounds(
    left: SideFit, right: SideFit, kernel_name: str
) -> Dict[str, float]:
        """Worst-case smoothing-bias bound for the jump.

        Local linear bias for the intercept is O(h^2); with a bound ``B`` on
        the second derivative of the conditional mean,

            |bias(mu_s)| <= B * h_s^2 * K_bias,  K_bias = mu2(K)/2,

        and the jump bias is bounded by the sum across sides.  We estimate B
        from the magnitude of curvature implied by the local slopes vs.
        bandwidth (a conservative local proxy) and report the bound so users
        can apply a bias-aware / MSE or CI adjustment explicitly rather than
        pretending smoothing bias is zero.
        """
        mu2 = {
            "uniform": 1.0 / 3.0,
            "triangular": 1.0 / 6.0,
            "epanechnikov": 1.0 / 5.0,
            "tricube": 70.0 / 429.0,
        }[kernel_name]
        k_bias = mu2 / 2.0
        # Local curvature proxy: |slope|/h (slope is the first derivative at
        # the boundary; scaled by h it has the order of the second derivative).
        b_l = abs(left.slope) / max(left.bandwidth, 1e-12)
        b_r = abs(right.slope) / max(right.bandwidth, 1e-12)
        bound = k_bias * (b_l * left.bandwidth**2 + b_r * right.bandwidth**2)
        return {
            "kernel_mu2": float(mu2),
            "curvature_proxy_left": float(b_l),
            "curvature_proxy_right": float(b_r),
            "bias_bound_abs": float(bound),
        }


def _failed(
    run_id: str, cutoff: float, kernel: str, method_name: str,
    hl: float | None, hr: float | None, se_type: str,
    category: FailureCategory, reason: str, diagnostics: Dict[str, Any],
) -> RDResult:
    return RDResult(
        run_id=run_id,
        status=RunStatus.FAILED,
        cutoff=cutoff,
        kernel=kernel,
        bandwidth_method=method_name,
        bandwidth_left=float(hl) if hl is not None else float("nan"),
        bandwidth_right=float(hr) if hr is not None else float("nan"),
        se_type=se_type,
        tau=None, se=None, ci=None, z=None, pvalue=None,
        left=None, right=None,
        failure_category=category,
        failure_reason=reason,
        warnings=[],
        diagnostics=diagnostics,
        versions=_versions(),
    )


def rd_estimate(
    x: Array,
    y: Array,
    *,
    cutoff: float = 0.0,
    kernel: str = "triangular",
    bandwidth: float | str = "rot",
    se_type: str = "hc2",
    run_id: str | None = None,
    alpha: float = 0.05,
) -> RDResult:
    """Estimate a sharp RD jump with side-specific local-linear fits.

    Parameters
    ----------
    x, y
        Running variable and outcome arrays (equal length, finite).
    cutoff
        Threshold value ``c``.
    kernel
        One of :data:`app.core.kernels.KERNELS`.
    bandwidth
        ``"rot"``, ``"ik"`` or a positive float applied per side.
    se_type
        ``const`` / ``hc1`` / ``hc2`` (default HC2 robust).
    run_id
        Caller-chosen identity; a UUID4 is generated otherwise.
    alpha
        Two-sided normal CI level (``1 - alpha``).
    """
    rid = run_id or f"rd-{uuid.uuid4().hex[:12]}"
    kernel = kernel.strip().lower()
    se_type = se_type.strip().lower()
    xv = np.asarray(x, dtype=np.float64)
    yv = np.asarray(y, dtype=np.float64)
    diag_extra: Dict[str, Any] = {}

    # ---- Input validation: fail fast with an explicit category -------------
    if se_type not in SUPPORTED_SE_TYPES:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.BAD_INPUT,
                       f"se_type must be one of {SUPPORTED_SE_TYPES}", {})
    try:
        get_kernel(kernel)
    except (ValueError, TypeError) as exc:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.BAD_INPUT, str(exc), {})
    if xv.ndim != 1 or yv.ndim != 1 or xv.shape != yv.shape:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.BAD_INPUT,
                       f"x and y must be 1-D of equal length, got {xv.shape}/{yv.shape}", {})
    # Finiteness is checked before counts: a NaN must read as bad input, not
    # be silently dropped into an "insufficient data" tally.
    if not (np.all(np.isfinite(xv)) and np.all(np.isfinite(yv))):
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.BAD_INPUT, "x/y contain non-finite values", {})
    if xv.size < 2 * MIN_SIDE_OBS:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.INSUFFICIENT_DATA,
                       f"need at least {2 * MIN_SIDE_OBS} observations, got {xv.size}", {})

    n_left = int(np.count_nonzero(xv < cutoff))
    n_right = int(np.count_nonzero(xv > cutoff))
    n_at = int(np.count_nonzero(xv == cutoff))
    diag_extra["sample"] = {
        "n": int(xv.size), "n_left": n_left, "n_right": n_right,
        "n_at_cutoff_excluded": n_at,
    }
    if n_left < MIN_SIDE_OBS or n_right < MIN_SIDE_OBS:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.INSUFFICIENT_DATA,
                       f"need >= {MIN_SIDE_OBS} obs strictly on each side "
                       f"(left={n_left}, right={n_right})", diag_extra)

    # ---- Bandwidth ----------------------------------------------------------
    method_name = "fixed"
    try:
        if isinstance(bandwidth, str):
            choice = select_bandwidth(xv, yv, cutoff, bandwidth, kernel)
            method_name = choice.method
            if choice.fell_back:
                diag_extra["bandwidth_fallback"] = {
                    "from": "ik", "to": "rot", "reason": choice.fallback_reason,
                }
            hl, hr = choice.h_left, choice.h_right
            diag_extra["bandwidth_details"] = choice.details
        else:
            hval = float(bandwidth)
            if not np.isfinite(hval) or hval <= 0:
                raise ValueError
            hl = hr = hval
    except (ValueError, TypeError) as exc:
        return _failed(rid, cutoff, kernel, str(bandwidth), None, None, se_type,
                       FailureCategory.BAD_INPUT, f"invalid bandwidth: {exc}",
                       diag_extra)

    # ---- Per-side local-linear fits ----------------------------------------
    left_dist, left_y = cutoff - xv[xv < cutoff], yv[xv < cutoff]
    right_dist, right_y = xv[xv > cutoff] - cutoff, yv[xv > cutoff]

    # Feasibility + numerical-stability expansion per side:
    # widen a selected bandwidth just enough to (a) cover
    # >= MIN_DISTINCT_SUPPORT distinct support points and (b) yield a
    # numerically well-conditioned local design. Every expansion is recorded
    # as a diagnostic adjustment, never done silently.
    sides: Dict[str, SideFit] = {}
    final_bw: Dict[str, float] = {}
    adjustments: Dict[str, Any] = {}
    extrapolation: Dict[str, float] = {}
    for side_name, dist, vy, selected in (
        ("left", left_dist, left_y, hl), ("right", right_dist, right_y, hr),
    ):
        unique = np.unique(dist)
        unique = unique[unique > 0.0]
        if unique.size < MIN_DISTINCT_HARD:
            # Observations exist on this side but it has no usable variation
            # in the running variable: that is a rank-deficient design, not a
            # lack of data.
            return _failed(rid, cutoff, kernel, method_name, hl, hr, se_type,
                           FailureCategory.RANK_DEFICIENT,
                           f"{side_name} side: only {unique.size} distinct "
                           f"support point(s) within support - the local "
                           "linear design has no slope variation", diag_extra)

        # Smallest window covering the target number of distinct points.
        target_idx = min(MIN_DISTINCT_SUPPORT, unique.size) - 1
        h_cur = max(float(selected), float(unique[target_idx]) * (1.0 + 1e-12))
        steps = 0
        fit: SideFit | None = None
        reason = ""
        # Widen further only while the local design is numerically unhealthy.
        while True:
            try:
                fit = _fit_side(dist, vy, h_cur, kernel, side_name)
                reason = ""
                break
            except FitError as exc:
                reason = str(exc)
                covered = int(np.searchsorted(unique, h_cur, side="right"))
                if covered >= unique.size:
                    cat = (FailureCategory.RANK_DEFICIENT if "rank" in reason
                           else FailureCategory.BANDWIDTH_INFEASIBLE)
                    return _failed(rid, cutoff, kernel, method_name, hl, hr,
                                   se_type, cat,
                                   f"{side_name} side: {reason} (whole support used)",
                                   diag_extra)
                h_cur = float(unique[min(covered + 2, unique.size - 1)]) \
                    * (1.0 + 1e-12)
                steps += 1

        assert fit is not None
        # Identifiability from the chosen (smallest healthy) window:
        # extrapolation factor = empty gap / span of data actually used.
        gap = float(unique[0])
        used = unique[unique <= h_cur]
        span = float(used[-1] - used[0]) if used.size >= 2 else 0.0
        factor = gap / span if span > 0 else float("inf")
        extrapolation[side_name] = factor
        if factor >= EXT_FAIL_FACTOR:
            diag_extra.setdefault("identifiability", {})[side_name] = {
                "closest_distance": gap,
                "used_span": span,
                "extrapolation_factor": factor,
                "bandwidth": h_cur,
            }
            return _failed(
                rid, cutoff, kernel, method_name, hl, hr, se_type,
                FailureCategory.NON_IDENTIFIABLE,
                f"{side_name} side: closest observation is {gap:.4g} from the "
                f"cutoff but the smallest healthy local window spans only "
                f"{span:.4g} (extrapolation factor {factor:.2f} >= 1); the "
                "boundary level is not nonparametrically identified across "
                "the empty gap - a parametric extrapolation would be required",
                diag_extra)

        if h_cur > float(selected) * (1.0 + 1e-9):
            adjustments[side_name] = {
                "selected": float(selected),
                "adjusted_to": float(h_cur),
                "expansion_steps": steps,
                "n_distinct_in_window": int(used.size),
                "reason": "widen for >= 10 distinct points and a healthy design",
            }
        sides[side_name] = fit
        final_bw[side_name] = h_cur

    diag_extra["extrapolation_factor"] = dict(extrapolation)
    if adjustments:
        diag_extra["bandwidth_adjustments"] = adjustments
    hl, hr = final_bw["left"], final_bw["right"]
    left, right = sides["left"], sides["right"]

    # ---- Jump, variance combination, normal CI -----------------------------
    se_l, se_r = _se_of(left, se_type), _se_of(right, se_type)
    se = float(np.hypot(se_l, se_r))
    tau = right.intercept - left.intercept
    if not np.isfinite(se) or se <= 0:
        z, p, ci = float("nan"), float("nan"), None
    else:
        z = (tau - 0.0) / se
        p = float(2 * (1 - stats.norm.cdf(abs(z))))
        zcrit = float(stats.norm.ppf(1 - alpha / 2))
        ci = (tau - zcrit * se, tau + zcrit * se)

    # ---- Diagnostics & status ----------------------------------------------
    warnings: list[str] = []
    if adjustments:
        warnings.append(
            "bandwidth widened above the selected value on "
            f"{', '.join(sorted(adjustments))} side(s) to include "
            f">={MIN_DISTINCT_SUPPORT} distinct support points "
            "(see diagnostics.bandwidth_adjustments)"
        )
    # Sparse-boundary warning for moderate extrapolation (factor in
    # [EXT_WARN_FACTOR, EXT_FAIL_FACTOR)); the hard failure case is handled
    # per side above before estimation.
    for side in (left, right):
        factor = extrapolation[side.side]
        if factor >= EXT_WARN_FACTOR:
            warnings.append(
                f"{side.side} side: boundary level extrapolates across a gap "
                f"of {side.support_min:.4g}, {factor:.2f}x the local data "
                f"span; interpret the boundary intercept and jump with "
                "caution (see diagnostics.extrapolation_factor)"
            )
    for diag in run_all_diagnostics(xv, cutoff, kernel):
        if diag.warning:
            warnings.append(diag.warning)
        diag_extra[diag.name] = diag.to_dict()
    diag_extra["bias"] = _bias_bounds(left, right, kernel)
    diag_extra["identifiable_range"] = {
        "left": [cutoff - left.support_max, cutoff - left.support_min],
        "right": [cutoff + right.support_min, cutoff + right.support_max],
        "note": "x-range carrying positive kernel weight on each side",
    }
    diag_extra["effective_sample"] = {
        "left_n": left.n, "right_n": right.n,
        "left_sum_weights": left.sum_weights, "right_sum_weights": right.sum_weights,
    }

    status = RunStatus.WARNING if warnings else RunStatus.SUCCESS
    return RDResult(
        run_id=rid,
        status=status,
        cutoff=cutoff,
        kernel=kernel,
        bandwidth_method=method_name,
        bandwidth_left=hl,
        bandwidth_right=hr,
        se_type=se_type,
        tau=float(tau),
        se=se if np.isfinite(se) else None,
        ci=ci,
        z=float(z) if np.isfinite(z) else None,
        pvalue=p if np.isfinite(p) else None,
        left=left,
        right=right,
        failure_category=None,
        failure_reason=None,
        warnings=warnings,
        diagnostics=diag_extra,
        versions=_versions(),
    )


def result_to_json(result: RDResult) -> str:
    """Stable JSON serialisation used by storage and logs."""
    return json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False)
