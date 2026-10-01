"""RD diagnostics: discrete running variable, cutoff heaping, McCrary test.

Three checks are reported independently - none of them silently downgrades a
run; each contributes structured warnings instead.

1. ``discreteness`` : share of distinct values, mass concentration, share
                      exactly at the cutoff.
2. ``heaping``      : excess mass exactly at the cutoff relative to the two
                      adjacent mass points (Poisson-rate comparison).
3. ``mccrary``      : McCrary (2008) density-continuity test - bin counts,
                      kernel-weighted local-linear *Poisson* log-density
                      regression per side, jump in log density, Fisher-
                      information SE, z and p-value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import numpy as np
from scipy import stats
from numpy.typing import NDArray

from .kernels import get_kernel
from .bandwidth import _rot_side

Array = NDArray[np.float64]

DISCRETE_UNIQUE_RATIO = 0.10     # <10% distinct values -> coarse/discrete
DISCRETE_MAX_MASS = 0.05         # one value holding >5% -> lumpy
HEAPING_Z = 1.96
MCCRARY_ALPHA = 0.05


@dataclass(frozen=True)
class DiagnosticResult:
    name: str
    passed: bool | None              # None = not assessable
    details: Dict[str, Any] = field(default_factory=dict)
    warning: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "warning": self.warning,
            "details": self.details,
        }


def check_discreteness(x: Array, cutoff: float) -> DiagnosticResult:
    """Assess how coarsely the running variable is recorded."""
    n = x.size
    unique, counts = np.unique(x, return_counts=True)
    unique_ratio = unique.size / n
    max_mass = float(counts.max() / n)
    at_cutoff = int(np.count_nonzero(x == cutoff))
    at_cutoff_share = at_cutoff / n
    discrete = unique_ratio < DISCRETE_UNIQUE_RATIO or max_mass > DISCRETE_MAX_MASS

    warning = None
    if discrete:
        warning = (
            f"running variable looks discrete/heaped: {unique.size} distinct "
            f"values among {n} (ratio {unique_ratio:.3f}), largest mass point "
            f"{max_mass:.3f}; cluster-robust inference or discrete methods advised"
        )
    return DiagnosticResult(
        "discreteness",
        passed=not discrete,
        details={
            "n": n,
            "n_unique": int(unique.size),
            "unique_ratio": float(unique_ratio),
            "max_point_mass": max_mass,
            "n_at_cutoff": at_cutoff,
            "share_at_cutoff": float(at_cutoff_share),
            "thresholds": {
                "unique_ratio_below": DISCRETE_UNIQUE_RATIO,
                "max_mass_above": DISCRETE_MAX_MASS,
            },
        },
        warning=warning,
    )


def check_heaping(x: Array, cutoff: float) -> DiagnosticResult:
    """Test for abnormal pile-up of observations exactly at the cutoff.

    Observations exactly at the cutoff have ambiguous treatment status and
    are excluded from both local fits. We test whether their count is
    excessive relative to the average mass at the nearest occupied value on
    each side using a Poisson-rate comparison (reported as a z-statistic).
    """
    unique, counts = np.unique(x, return_counts=True)
    at = int(counts[unique == cutoff].sum())
    left_vals = unique[unique < cutoff]
    right_vals = unique[unique > cutoff]
    if left_vals.size == 0 or right_vals.size == 0:
        return DiagnosticResult(
            "heaping", None, {"n_at_cutoff": at},
            "cannot assess heaping: no occupied value on one side",
        )
    neighbor_counts = np.array([
        counts[unique == left_vals[-1]][0],
        counts[unique == right_vals[0]][0],
    ], dtype=float)
    expected = float(neighbor_counts.mean())
    if expected <= 0:
        z = float("inf") if at > 0 else 0.0
        p = 0.0 if at > 0 else 1.0
    else:
        z = (at - expected) / np.sqrt(expected)
        p = float(2 * (1 - stats.norm.cdf(abs(z))))
    flagged = abs(z) > HEAPING_Z and at > expected
    return DiagnosticResult(
        "heaping",
        passed=not flagged,
        details={
            "n_at_cutoff": at,
            "neighbor_counts": [int(neighbor_counts[0]), int(neighbor_counts[1])],
            "expected_at_cutoff": expected,
            "z": float(z),
            "pvalue": p,
        },
        warning=(
            f"excess mass at cutoff ({at} vs expected {expected:.1f}, z={z:.2f}); "
            "these observations are excluded from estimation - check manipulation"
            if flagged else None
        ),
    )


def _bin_counts(x: Array, cutoff: float, bin_width: float) -> Tuple[Array, Array]:
    """Histogram on a grid aligned so the cutoff is a bin *boundary*."""
    n_left = int(np.ceil((cutoff - x.min()) / bin_width)) + 1
    n_right = int(np.ceil((x.max() - cutoff) / bin_width)) + 1
    left_edges = cutoff - np.arange(0, n_left)[::-1] * bin_width
    right_edges = cutoff + np.arange(0, n_right) * bin_width
    edges = np.concatenate([left_edges[:-1], right_edges])
    counts, edges = np.histogram(x, bins=edges)
    midpoints = 0.5 * (edges[:-1] + edges[1:])
    return midpoints, counts.astype(float)


def _local_poisson_boundary(
    dist: Array, cell_counts: Array, h: float, kernel_fn
) -> Tuple[float, float]:
    """Kernel-weighted local-linear Poisson regression evaluated at 0.

    Cell counts follow ``Y_j ~ Poisson(mu_j)`` with ``log mu_j = a + b*d_j``
    and observation weight ``K(d_j/h)``. Solved by Fisher-scoring IRLS; the
    intercept SE comes from the inverse Fisher information, which reflects
    Poisson count uncertainty correctly (a Gaussian WLS on log counts does
    not, and zero-count cells enter naturally here).
    """
    inside = (dist > 0.0) & (dist <= h)
    d, y = dist[inside], cell_counts[inside]
    if d.size < 8 or np.sum(y) < 10.0:
        raise ValueError(
            f"insufficient density cells ({d.size}), total count {np.sum(y):.0f}"
        )
    kw = kernel_fn(d / h)
    X = np.column_stack([np.ones(d.size), d])
    beta = np.array([np.log(max(y.mean(), 1e-6)), 0.0])
    for _ in range(50):
        eta = np.clip(X @ beta, -50.0, 50.0)
        mu = np.exp(eta)
        w = kw * mu                                    # Fisher weight
        z_work = eta + (y - mu) / mu                  # working response
        Xw = X * np.sqrt(w)[:, None]
        beta_new, _, rank, _ = np.linalg.lstsq(Xw, z_work * np.sqrt(w), rcond=None)
        if rank < 2:
            raise ValueError("Poisson fit rank-deficient")
        if np.max(np.abs(beta_new - beta)) < 1e-10:
            beta = beta_new
            break
        beta = beta_new
    mu = np.exp(np.clip(X @ beta, -50.0, 50.0))
    info = X.T @ ((kw * mu)[:, None] * X)
    try:
        cov = np.linalg.inv(info)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"singular Poisson information matrix: {exc}") from exc
    return float(beta[0]), float(np.sqrt(max(cov[0, 0], 0.0)))


def mccrary_density_test(
    x: Array,
    cutoff: float,
    kernel: str = "triangular",
    bin_width: float | None = None,
    min_bins: int = 8,
    min_bins_in_window: int = 8,
) -> DiagnosticResult:
    """McCrary (2008) sorting test via local-likelihood density estimation.

    Step 1: coarsen into bins on a grid aligned to the cutoff.
    Step 2: kernel-weighted local-linear *Poisson* regression of cell counts
    on midpoints per side, evaluated at the cutoff; the statistic is the
    jump ``theta = log f+(c) - log f-(c)`` with per-side Fisher-information
    SEs combined under independence.

    The smoothing window starts from a ROT rule and is widened just enough to
    contain ``min_bins_in_window`` occupied cells; a too-narrow window lets a
    single boundary cell dominate (the undersmoothing concern of McCrary 08).
    """
    kernel_fn = get_kernel(kernel)
    xv = np.asarray(x, float)
    n = xv.size
    if bin_width is None:
        sd = float(np.std(xv, ddof=1))               # McCrary coarsening rule
        bin_width = 2.0 * sd / np.sqrt(n) if sd > 0 else 1.0
    if bin_width <= 0:
        return DiagnosticResult("mccrary", None, {"bin_width": bin_width},
                                "non-positive bin width")

    midpoints, counts = _bin_counts(xv, cutoff, bin_width)
    dist = midpoints - cutoff

    # All cells are retained; the Poisson likelihood handles zero counts
    # explicitly, which matters for coarse/discrete running variables.
    left_d, left_c = -dist[dist < 0], counts[dist < 0]
    right_d, right_c = dist[dist > 0], counts[dist > 0]
    left_occ = int(np.count_nonzero(left_c > 0))
    right_occ = int(np.count_nonzero(right_c > 0))
    if left_occ < min_bins or right_occ < min_bins:
        return DiagnosticResult(
            "mccrary", None,
            {"occupied_bins_left": left_occ, "occupied_bins_right": right_occ,
             "bin_width": float(bin_width)},
            "too few occupied bins to estimate log-density on both sides",
        )

    def _window(d_side: Array) -> float:
        occupied = np.sort(d_side[d_side > 0.0])
        h = min(_rot_side(occupied), float(occupied.max()))
        n_in = int(np.count_nonzero(occupied <= h))
        if n_in < min_bins_in_window and occupied.size >= min_bins_in_window:
            h = float(occupied[min_bins_in_window - 1]) * (1.0 + 1e-12)
        return h

    h_l, h_r = _window(left_d), _window(right_d)
    try:
        a_l, se_l = _local_poisson_boundary(left_d, left_c, h_l, kernel_fn)
        a_r, se_r = _local_poisson_boundary(right_d, right_c, h_r, kernel_fn)
    except ValueError as exc:
        return DiagnosticResult("mccrary", None, {"bin_width": float(bin_width)},
                                f"Poisson log-density fit failed: {exc}")

    theta = a_r - a_l
    se = float(np.hypot(se_r, se_l))
    z = theta / se if se > 0 else float("nan")
    p = float(2 * (1 - stats.norm.cdf(abs(z)))) if np.isfinite(z) else float("nan")
    ci = (theta - 1.96 * se, theta + 1.96 * se)
    flagged = bool(np.isfinite(p) and p < MCCRARY_ALPHA)
    return DiagnosticResult(
        "mccrary",
        passed=not flagged,
        details={
            "method": "kernel-weighted local-linear Poisson IRLS",
            "bin_width": float(bin_width),
            "n_bins": int(counts.size),
            "n_empty_bins": int(np.count_nonzero(counts == 0)),
            "occupied_bins_left": left_occ,
            "occupied_bins_right": right_occ,
            "bandwidth_left": float(h_l),
            "bandwidth_right": float(h_r),
            "theta_log_density_jump": float(theta),
            "se": se,
            "z": float(z),
            "pvalue": p,
            "ci95": [float(ci[0]), float(ci[1])],
            "density_ratio": float(np.exp(theta)),
            "log_density_left": a_l,
            "log_density_right": a_r,
        },
        warning=(
            f"McCrary density discontinuity: theta={theta:.3f}, z={z:.2f}, "
            f"p={p:.4f} (possible sorting/manipulation around cutoff)"
            if flagged else None
        ),
    )


def run_all_diagnostics(x: Array, cutoff: float, kernel: str) -> list[DiagnosticResult]:
    return [
        check_discreteness(x, cutoff),
        check_heaping(x, cutoff),
        mccrary_density_test(x, cutoff, kernel),
    ]
