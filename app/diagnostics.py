"""Evidence and diagnostics.

These checks address the specific failure modes an RD design must own up to:

1. ``discrete_running_var`` / ``mass_at_cutoff``
   A lattice-valued or heaped running variable invalidates the continuous-
   density assumption and exact-cutoff mass is non-ignorable for manipulation.

2. ``density_discontinuity``
   McCrary (2008) density test: histogram the running variable into fine
   bins, fit *separate* weighted local-linear regressions of log density at
   the cutoff and test their difference. A significant log-density jump is
   evidence of sorting (it is not, on its own, proof, and on a discrete
   runner the test is flagged as unreliable).

3. ``sparse_side`` / ``effective_sample``
   Count and kernel-weighted mass inside the chosen window on each side.

4. ``identification_range``
   The actual window spanned on each side and the largest gap between
   ordered observations nearest the cutoff: an estimate whose side window
   bridges a hole in the support is extrapolation, not identification.

5. ``poor_condition_number``
   Surfaced from the fits.

Nothing here is downgraded to "success": every check returns a structured
severity that the pipeline attaches verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from app.contract import DiagnosticCode, Severity
from app.estimator import SideResult

LATTICE_REL_TOL = 1e-6
MIN_UNIQUE_FRACTION = 0.25  # fewer distinct values than n/4 -> treated discrete


@dataclass(frozen=True)
class DensityTestResult:
    theta: float  # log-density jump at the cutoff
    se: float
    z: float
    p_value: float
    bin_width: float
    n_bins_left: int
    n_bins_right: int
    interpretable: bool
    note: str


def is_lattice(values: np.ndarray) -> tuple[bool, float]:
    """Detect an equally-spaced support via a gcd of sorted spacings."""
    uniq = np.unique(values)
    if uniq.size < 3:
        return True, 0.0
    gaps = np.diff(uniq)
    gaps = gaps[gaps > 1e-12]
    if gaps.size == 0:
        return True, 0.0
    step = float(np.min(gaps))
    residual = np.abs(gaps / step - np.round(gaps / step))
    lattice = bool(np.all(residual < 1e-5))
    return lattice, step


def discreteness_diagnostics(
    x: np.ndarray, cutoff: float
) -> list[dict]:
    out: list[dict] = []
    n = x.size
    uniq = np.unique(x)
    lattice, step = is_lattice(x)
    discrete = lattice or uniq.size < max(4, int(MIN_UNIQUE_FRACTION * n))
    if discrete:
        out.append(
            {
                "code": DiagnosticCode.DISCRETE_RUNNING_VAR,
                "severity": Severity.WARNING,
                "message": (
                    f"Running variable is effectively discrete: "
                    f"{uniq.size} distinct values among {n} observations"
                    + (f" on a lattice of spacing ~{step:g}" if lattice else "")
                    + ". Local-linear interpolation across gaps and the "
                    "McCrary test are less reliable; cluster CIs by value."
                ),
                "details": {
                    "n_distinct": int(uniq.size),
                    "n": int(n),
                    "lattice": lattice,
                    "lattice_spacing": step,
                },
            }
        )
    at = int(np.sum(x == cutoff))
    if at > 0:
        share = at / n
        sev = Severity.CRITICAL if share > 0.05 else Severity.WARNING
        out.append(
            {
                "code": DiagnosticCode.MASS_AT_CUTOFF,
                "severity": sev,
                "message": (
                    f"{at} observations ({share:.2%}) sit exactly at the cutoff. "
                    "They are assigned to the treated side by the x>=c rule but "
                    "their presence is consistent with heaping/manipulation; "
                    "report estimates with and without them."
                ),
                "details": {"n_at_cutoff": at, "share": share},
            }
        )
    return out


def _mccrary_side(
    dist: np.ndarray, sign_bin_centres: np.ndarray, counts: np.ndarray, h: float
) -> tuple[float, float, int]:
    """Weighted local linear fit of log bin density at distance 0.

    Returns (log f(c), robust se, positive bins used). Bins are weighted by
    their count (McCrary frequency weighting) times a triangular kernel in
    bin distance. Zero-count bins cannot enter in log scale and are dropped;
    if too few positive bins remain the test is marked uninterpretable.
    """
    positive = counts > 0
    d = sign_bin_centres[positive]
    cts = counts[positive].astype(float)
    if d.size < 3:
        return np.nan, np.nan, int(d.size)
    log_f = np.log(cts)  # bin width factor is common to both sides -> cancels
    k = np.clip(1.0 - np.abs(d) / h, 0.0, 1.0)
    keep = k > 0
    d, cts, log_f, k = d[keep], cts[keep], log_f[keep], k[keep]
    if d.size < 3:
        return np.nan, np.nan, int(d.size)
    X = np.column_stack([np.ones_like(d), d])
    w = k * cts  # triangular kernel * frequency weight
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], log_f * sw, rcond=None)
    resid = log_f - X @ beta
    XtWX = (X * w[:, None]).T @ X
    inv = np.linalg.inv(XtWX)
    meat = (X * (w * resid)[:, None]).T @ (X * (w * resid)[:, None])
    vcov = inv @ meat @ inv
    return float(beta[0]), float(np.sqrt(max(vcov[0, 0], 0.0))), int(d.size)


def mccrary_density_test(x: np.ndarray, cutoff: float) -> DensityTestResult:
    left = x[x < cutoff]
    right = x[x > cutoff]
    if left.size < 5 or right.size < 5:
        return DensityTestResult(
            np.nan, np.nan, np.nan, np.nan, np.nan, left.size, right.size,
            False, "fewer than 5 strict observations on a side; test skipped",
        )
    dl, dr = cutoff - left, right - cutoff
    sd = max(np.std(np.concatenate([dl, dr])), 1e-12)
    iqr = np.subtract(
        *np.percentile(np.concatenate([dl, dr]), [75, 25])
    )
    scale = iqr / 1.349 if iqr > 0 else sd
    n = x.size
    # McCrary bin width (fine histogram) and smoothing width (pilot scale).
    bin_width = 2.0 * scale * n ** (-1.0 / 2.0)
    h = 4.0 * scale * n ** (-1.0 / 5.0)
    bin_width = max(bin_width, 1e-12)

    def bins(dist: np.ndarray) -> np.ndarray:
        edges = np.arange(0.0, dist.max() + bin_width, bin_width)
        counts, _ = np.histogram(dist, bins=edges)
        centres = edges[:-1] + bin_width / 2.0
        # Pack into (centre, count) aligned arrays.
        return centres, counts

    cl, nl = bins(dl)
    cr, nr = bins(dr)
    log_l, se_l, used_l = _mccrary_side(dl, cl, nl, h)
    log_r, se_r, used_r = _mccrary_side(dr, cr, nr, h)
    if not (np.isfinite(log_l) and np.isfinite(log_r)):
        return DensityTestResult(
            np.nan, np.nan, np.nan, np.nan, bin_width, used_l, used_r,
            False, "too few populated bins near the cutoff for log-density fit",
        )
    theta = log_r - log_l
    se = float(np.sqrt(se_l**2 + se_r**2))
    z = theta / se if se > 0 else np.nan
    p = float(2 * (1 - stats.norm.cdf(abs(z)))) if np.isfinite(z) else np.nan
    return DensityTestResult(
        theta=theta, se=se, z=z, p_value=p, bin_width=bin_width,
        n_bins_left=used_l, n_bins_right=used_r, interpretable=True,
        note="separate triangular WLS fits of log histogram density at c",
    )


def density_diagnostic(x: np.ndarray, cutoff: float, alpha: float = 0.05) -> dict:
    res = mccrary_density_test(x, cutoff)
    lattice, _ = is_lattice(x)
    if not res.interpretable:
        severity = Severity.WARNING
        message = f"Density test not interpretable: {res.note}"
    elif np.isfinite(res.p_value) and res.p_value < alpha:
        severity = Severity.WARNING
        message = (
            f"McCrary density discontinuity: log-density jump theta={res.theta:.3f} "
            f"(z={res.z:.2f}, p={res.p_value:.4f}) suggests sorting at the cutoff."
        )
    else:
        severity = Severity.INFO
        message = (
            f"No density discontinuity detected: theta={res.theta:.3f}, "
            f"p={res.p_value:.3f} (not evidence of no manipulation)."
        )
    if lattice and res.interpretable:
        message += " Runner is lattice-valued; treat the test as indicative only."
    return {
        "code": DiagnosticCode.DENSITY_DISCONTINUITY,
        "severity": severity,
        "message": message,
        "details": {
            "theta": None if not np.isfinite(res.theta) else res.theta,
            "se": None if not np.isfinite(res.se) else res.se,
            "z": None if not np.isfinite(res.z) else res.z,
            "p_value": None if not np.isfinite(res.p_value) else res.p_value,
            "bin_width": res.bin_width,
            "n_bins_left": res.n_bins_left,
            "n_bins_right": res.n_bins_right,
            "interpretable": res.interpretable,
            "lattice_runner": lattice,
        },
    }


def support_diagnostics(
    left: SideResult, right: SideResult, min_obs: int
) -> list[dict]:
    out: list[dict] = []
    for res, name in ((left, "left"), (right, "right")):
        if res.n < min_obs:
            out.append(
                {
                    "code": DiagnosticCode.SPARSE_SIDE,
                    "severity": Severity.CRITICAL,
                    "message": (
                        f"{name} side has only {res.n} points in the window "
                        f"(minimum {min_obs}); local fit is data-starved."
                    ),
                    "details": {
                        "side": name,
                        "n_in_window": res.n,
                        "effective_n": res.effective_n,
                        "minimum_required": min_obs,
                    },
                }
            )
        elif res.effective_n < min_obs:
            out.append(
                {
                    "code": DiagnosticCode.SPARSE_SIDE,
                    "severity": Severity.WARNING,
                    "message": (
                        f"{name} side effective N (sum of kernel weights) "
                        f"{res.effective_n:.1f} below {min_obs} despite "
                        f"{res.n} raw points; most weight sits at the edge."
                    ),
                    "details": {
                        "side": name,
                        "n_in_window": res.n,
                        "effective_n": res.effective_n,
                    },
                }
            )
        if res.condition_number > 1e8:
            out.append(
                {
                    "code": DiagnosticCode.POOR_CONDITION_NUMBER,
                    "severity": Severity.WARNING,
                    "message": (
                        f"{name} weighted design condition number "
                        f"{res.condition_number:.2e} is high."
                    ),
                    "details": {
                        "side": name,
                        "condition_number": res.condition_number,
                    },
                }
            )
    return out


def identification_range(left: SideResult, right: SideResult) -> dict:
    """Report window span and the nearest-cutoff support gap on each side."""

    def gap_info(res: SideResult) -> dict:
        d = np.sort(np.abs(res.x - res.cutoff))
        if d.size < 2:
            return {"span": float(d[-1]) if d.size else 0.0, "max_gap": None}
        gaps = np.diff(d)
        return {
            "span": float(d[-1]),
            "max_gap": float(gaps.max()),
            "nearest_point": float(d[0]),
        }

    gl, gr = gap_info(left), gap_info(right)
    severity = Severity.INFO
    worst_gap = max(g for g in (gl["max_gap"], gr["max_gap"]) if g is not None)
    if worst_gap > 0.25 * max(left.bandwidth, right.bandwidth):
        severity = Severity.WARNING
    return {
        "code": DiagnosticCode.IDENTIFICATION_RANGE,
        "severity": severity,
        "message": (
            f"Identified on left within {gl['span']:.4g} and right within "
            f"{gr['span']:.4g} of the cutoff; largest inner support gap "
            f"{worst_gap:.4g}."
        ),
        "details": {"left": gl, "right": gr},
    }


def effective_sample(left: SideResult, right: SideResult) -> dict:
    return {
        "code": DiagnosticCode.EFFECTIVE_SAMPLE,
        "severity": Severity.INFO,
        "message": (
            f"Effective N (sum of kernel weights): left={left.effective_n:.1f} "
            f"of {left.n} in-window, right={right.effective_n:.1f} of {right.n}."
        ),
        "details": {
            "left": {"n_in_window": left.n, "effective_n": left.effective_n},
            "right": {"n_in_window": right.n, "effective_n": right.effective_n},
        },
    }
