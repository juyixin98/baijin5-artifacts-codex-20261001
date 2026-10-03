"""End-to-end translation estimate: kernel + subpixel + confidence policy.

Status model (contract items 2 and 3):

* ``failed``    — hard failures present; no shift is reported.
* ``uncertain`` — a shift is reported but at least one uncertainty is
  listed (ambiguity, marginal PSR, low overlap, unreliable subpixel fit).
* ``ok``        — no failures, no uncertainties, confidence above threshold.

Brightness change vs. true non-overlap: per-image mean/std normalisation
makes the kernel invariant to global gain/offset, so a brightness-changed
pair still scores a high PSR and full overlap. A genuinely non-overlapping
(or barely overlapping) pair cannot: it surfaces as LOW_PSR and/or
INSUFFICIENT_OVERLAP. The two cases are therefore distinguishable in the
result, not just in hindsight.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import scipy

from app.config import KernelConfig
from app.kernel.phasecorr import Peak, cross_power, normalized_surface
from app.kernel.subpixel import estimate_subpixel

DEGENERATE_SPECTRUM = "DEGENERATE_SPECTRUM"
LOW_PSR = "LOW_PSR"
INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"
AMBIGUOUS_PEAKS = "AMBIGUOUS_PEAKS"
UNRELIABLE_SUBPIXEL = "UNRELIABLE_SUBPIXEL"
LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass
class EstimateResult:
    status: str  # "ok" | "uncertain" | "failed"
    shift: tuple[float, float] | None
    integer_shift: tuple[int, int] | None
    subpixel_offset: tuple[float, float] | None
    confidence: float
    psr: float | None
    second_peak_ratio: float | None
    overlap_fraction: float | None
    peaks: list[Peak] = field(default_factory=list)
    ambiguity_peaks: list[Peak] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def _peak_signal_ratio(
    surface: np.ndarray, iy: int, ix: int, radius: int
) -> float:
    h, w = surface.shape
    yy, xx = np.mgrid[0:h, 0:w]
    exclusion = (yy - iy) ** 2 + (xx - ix) ** 2 <= radius**2
    rest = surface[~exclusion]
    mu, sigma = float(rest.mean()), float(rest.std())
    peak = float(surface[iy, ix])
    if sigma <= 0.0:
        return math.inf if peak > mu else 0.0
    return (peak - mu) / sigma


def _overlap_fraction(shape: tuple[int, int], dy: float, dx: float) -> float:
    h, w = shape
    oy = max(0.0, h - abs(dy))
    ox = max(0.0, w - abs(dx))
    return (oy * ox) / (h * w)


def _confidence(psr: float, second_ratio: float, cfg: KernelConfig) -> float:
    if math.isinf(psr):
        psr_term = 1.0
    else:
        span = 2.0 * cfg.psr_ok - cfg.psr_failure
        psr_term = min(1.0, max(0.0, (psr - cfg.psr_failure) / span))
    # A strong runner-up peak halves the trust even when the PSR is high.
    return round(psr_term * (1.0 - 0.5 * min(second_ratio, 1.0)), 4)


def estimate_shift(
    img1: np.ndarray, img2: np.ndarray, cfg: KernelConfig
) -> EstimateResult:
    t0 = time.perf_counter()
    cp = cross_power(img1, img2, cfg)
    # Estimation surface: low magnitude floor, drives the shift itself.
    est_surf = normalized_surface(cp, cfg.eps_ratio, cfg.peak_count)
    # Ambiguity surface: strict floor, exposes lattice replica peaks.
    amb_surf = normalized_surface(cp, cfg.ambiguity_eps_ratio, cfg.peak_count)
    my, mx = est_surf.max_shift
    iy = est_surf.integer_shift[0] + my
    ix = est_surf.integer_shift[1] + mx

    diagnostics = {
        "shape": list(img1.shape),
        "window": cfg.window,
        "pad_factor": cfg.pad_factor,
        "valid_shift_range": {"dy": [-my, my], "dx": [-mx, mx]},
        "degenerate_fraction": round(est_surf.degenerate_fraction, 6),
        "ambiguity_degenerate_fraction": round(amb_surf.degenerate_fraction, 6),
        "eps": est_surf.eps,
        "ambiguity_eps": amb_surf.eps,
        "kernel_versions": {"numpy": np.__version__, "scipy": scipy.__version__},
    }

    failures: list[str] = []
    uncertainties: list[str] = []

    if (
        est_surf.degenerate_fraction >= cfg.max_degenerate_fraction
        or est_surf.peak_value <= 0.0
    ):
        failures.append(DEGENERATE_SPECTRUM)
        diagnostics["elapsed_ms"] = round((time.perf_counter() - t0) * 1e3, 3)
        return EstimateResult(
            status="failed",
            shift=None,
            integer_shift=None,
            subpixel_offset=None,
            confidence=0.0,
            psr=None,
            second_peak_ratio=None,
            overlap_fraction=None,
            peaks=est_surf.peaks,
            ambiguity_peaks=amb_surf.peaks,
            failures=failures,
            uncertainties=uncertainties,
            diagnostics=diagnostics,
        )

    psr = _peak_signal_ratio(est_surf.surface, iy, ix, cfg.psr_exclusion_radius)
    if psr < cfg.psr_failure:
        failures.append(LOW_PSR)
    elif psr < cfg.psr_ok:
        uncertainties.append(LOW_PSR)

    sub = estimate_subpixel(est_surf.surface, iy, ix)
    if not sub.ok:
        uncertainties.append(UNRELIABLE_SUBPIXEL + ":" + ",".join(sub.failures))
    offset = sub.offset if sub.ok else (0.0, 0.0)

    second_ratio = 0.0
    if len(amb_surf.peaks) >= 2 and amb_surf.peaks[0].value > 0.0:
        second_ratio = amb_surf.peaks[1].value / amb_surf.peaks[0].value
    if second_ratio > cfg.ambiguity_ratio:
        uncertainties.append(AMBIGUOUS_PEAKS)

    dy = est_surf.integer_shift[0] + offset[0]
    dx = est_surf.integer_shift[1] + offset[1]
    overlap = _overlap_fraction(img1.shape, dy, dx)
    if overlap < cfg.hard_min_overlap:
        failures.append(INSUFFICIENT_OVERLAP)
    elif overlap < cfg.min_overlap:
        uncertainties.append(INSUFFICIENT_OVERLAP)

    confidence = _confidence(psr, second_ratio, cfg)

    if failures:
        status = "failed"
    elif uncertainties or confidence < cfg.confidence_ok:
        # An uncertain verdict must always name its reason; if the only
        # demotion is the confidence score itself, say so explicitly.
        if not uncertainties:
            uncertainties.append(LOW_CONFIDENCE)
        status = "uncertain"
    else:
        status = "ok"

    diagnostics["elapsed_ms"] = round((time.perf_counter() - t0) * 1e3, 3)
    return EstimateResult(
        status=status,
        shift=None if status == "failed" else (dy, dx),
        integer_shift=est_surf.integer_shift,
        subpixel_offset=offset,
        confidence=confidence,
        psr=None if math.isinf(psr) else round(psr, 3),
        second_peak_ratio=round(second_ratio, 4),
        overlap_fraction=round(overlap, 4),
        peaks=est_surf.peaks,
        ambiguity_peaks=amb_surf.peaks,
        failures=failures,
        uncertainties=uncertainties,
        diagnostics=diagnostics,
    )
