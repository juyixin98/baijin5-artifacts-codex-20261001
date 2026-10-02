"""End-to-end translation-estimation pipeline.

Orchestrates: input validation -> windowed/padded phase correlation ->
sub-pixel refinement -> ambiguity scan -> spatial-domain verification ->
status classification.  Every step appends to ``diagnostics.steps`` so the
API layer can expose *where* a conclusion came from.

Failure reasons and uncertainties are kept in separate lists, per the
behavioural contract: a result is never both "failed" and silently uncertain.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config import KernelConfig, DEFAULT_CONFIG
from . import phase_correlation as pc
from . import subpixel as sp
from . import verify as vf
from .types import EstimateStatus, FailureCategory, Uncertainty


@dataclass
class EstimateResult:
    status: EstimateStatus
    shift: tuple[float, float] | None = None          # (dy, dx), pixels
    failure_reason: FailureCategory | None = None
    uncertainties: list[Uncertainty] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # ambiguous peaks
    confidence: dict = field(default_factory=dict)
    brightness: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "shift": {"dy": self.shift[0], "dx": self.shift[1]} if self.shift else None,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "uncertainties": [u.value for u in self.uncertainties],
            "candidates": self.candidates,
            "confidence": self.confidence,
            "brightness": self.brightness,
            "diagnostics": self.diagnostics,
        }


class _StepLog:
    """Accumulates named, timed pipeline steps."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def add(self, name: str, detail: dict | None = None) -> None:
        self.steps.append({"name": name, "detail": detail or {}})

    def timed(self, name: str):
        return _TimedStep(self, name)


class _TimedStep:
    def __init__(self, log: _StepLog, name: str) -> None:
        self.log, self.name = log, name
        self.detail: dict = {}

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.log.add(self.name, {**self.detail,
                                 "duration_ms": round((time.perf_counter() - self._t0) * 1e3, 3)})


def _failed(reason: FailureCategory, log: _StepLog, confidence: dict | None = None) -> EstimateResult:
    return EstimateResult(
        status=EstimateStatus.FAILED,
        failure_reason=reason,
        confidence=confidence or {},
        diagnostics={"steps": log.steps},
    )


def estimate_translation(reference: np.ndarray, moving: np.ndarray,
                         config: KernelConfig = DEFAULT_CONFIG) -> EstimateResult:
    """Estimate the translation of ``moving`` relative to ``reference``."""
    log = _StepLog()

    # ---- 1. input validation -------------------------------------------
    with log.timed("validate_input") as step:
        ref = np.asarray(reference, dtype=np.float64)
        mov = np.asarray(moving, dtype=np.float64)
        if ref.ndim != 2 or mov.ndim != 2:
            return _failed(FailureCategory.INVALID_IMAGE, log)
        if ref.shape != mov.shape:
            step.detail.update(ref_shape=list(ref.shape), mov_shape=list(mov.shape))
            return _failed(FailureCategory.SHAPE_MISMATCH, log)
        if not (np.all(np.isfinite(ref)) and np.all(np.isfinite(mov))):
            return _failed(FailureCategory.INVALID_IMAGE, log)
        if min(ref.shape) < 8:
            return _failed(FailureCategory.INVALID_IMAGE, log)
        step.detail.update(shape=list(ref.shape))

    # ---- 2. flat-response gate ------------------------------------------
    with log.timed("flat_gate") as step:
        ref_std, mov_std = float(ref.std()), float(mov.std())
        step.detail.update(ref_std=ref_std, mov_std=mov_std)
        if ref_std < config.flat_input_std or mov_std < config.flat_input_std:
            return _failed(FailureCategory.FLAT_RESPONSE, log)

    # ---- 3. phase correlation (two declared surfaces) ---------------------
    # Padded surface: extends the unambiguous range to +/-n (declared padding).
    # Unpadded surface: padding is *our* artifact and it corrupts exactly-
    # periodic content (truncation mixes box-transform phases), so intrinsic
    # periodic ambiguity is measured on the unpadded surface instead.
    with log.timed("phase_correlation") as step:
        surf = pc.correlation_surface(ref, mov, apply_window=config.apply_window,
                                      pad_factor=config.pad_factor, eps_rel=config.eps_rel,
                                      remove_mean=config.remove_mean)
        surf_u = pc.correlation_surface(ref, mov, apply_window=config.apply_window,
                                        pad_factor=1, eps_rel=config.eps_rel,
                                        remove_mean=config.remove_mean)
        step.detail.update(
            padded_shape=list(surf.padded_shape),
            window="hann" if config.apply_window else "none",
            mean_removed=config.remove_mean,
            pad_factor=config.pad_factor,
            zero_bin_fraction=round(surf.cross_power.zero_bin_fraction, 6),
        )
        for tag, s in (("padded", surf), ("unpadded", surf_u)):
            # A uniform surface (e.g. only the DC bin survived normalisation)
            # has no localisable peak; contrast is std / mean|surface|.
            ratio = float(s.values.std() / (np.abs(s.values).mean() + 1e-300))
            step.detail[f"surface_contrast_{tag}"] = round(ratio, 6)
            if ratio < config.flat_surface_ratio:
                return _failed(FailureCategory.FLAT_RESPONSE, log)

    # ---- 4. peak scan, primary selection, sub-pixel refinement -------------
    uncertainties: list[Uncertainty] = []
    with log.timed("peak_scan") as step:
        found_u = pc.find_candidate_peaks(surf_u.values, config.ambiguity_ratio,
                                          config.max_candidates,
                                          config.candidate_mask_radius)
        if len(found_u) > 1:
            # Intrinsically ambiguous content (periodic texture): the estimate
            # is only defined modulo the period.  Primary = minimum-norm alias
            # (declared tie-break), refined on the unpadded surface where the
            # aliases are clean.
            uncertainties.append(Uncertainty.AMBIGUOUS_PEAKS)
            primary = pc.select_primary_peak(found_u)
            active_surf = surf_u
            found = found_u
            estimation_surface = "unpadded_ambiguous"
        else:
            # Single candidate: use the padded surface for full shift range.
            found = pc.find_candidate_peaks(surf.values, config.ambiguity_ratio,
                                            config.max_candidates,
                                            config.candidate_mask_radius)
            primary = pc.select_primary_peak(found)
            active_surf = surf
            estimation_surface = "padded"
        peak_idx = primary["index"]
        stats = pc.peak_statistics(active_surf.values, peak_idx,
                                   config.psr_exclude_radius)
        step.detail.update(integer_shift=list(primary["shift"]),
                           n_candidates=len(found),
                           estimation_surface=estimation_surface,
                           ambiguity_ratio=config.ambiguity_ratio,
                           peak_height=round(stats["peak_height"], 6),
                           peak_to_sidelobe=round(stats["peak_to_sidelobe"], 3))

    with log.timed("subpixel_refine") as step:
        sub = sp.subpixel_estimate(active_surf.values,
                                   active_surf.cross_power.normalized,
                                   peak_idx, rounds=config.subpixel_steps)
        dy, dx = sub["shift"]
        step.detail.update(shift=[round(dy, 4), round(dx, 4)],
                           refine_disagreement_px=round(sub["refine_disagreement_px"], 4))

    candidates = [{"shift": {"dy": c["shift"][0], "dx": c["shift"][1]},
                   "height": c["height"]} for c in found]
    confidence = {
        **stats,
        "refine_disagreement_px": sub["refine_disagreement_px"],
        "zero_bin_fraction": active_surf.cross_power.zero_bin_fraction,
    }

    # ---- 5. spatial-domain verification (before the border gate: a spurious
    #       peak near the border is more informatively reported as absent
    #       common content than as a border hit) ---------------------------
    brightness: dict = {}
    with log.timed("verify_overlap") as step:
        ver = vf.verify_alignment(
            ref, mov, dy, dx,
            ncc_threshold=config.ncc_threshold,
            brightness_gain_tol=config.brightness_gain_tol,
            brightness_offset_tol_frac=config.brightness_offset_tol_frac)
        if ver is None:
            return _failed(FailureCategory.NO_COMMON_CONTENT, log, confidence)
        step.detail.update(overlap_fraction=round(ver.overlap_fraction, 4),
                           ncc=round(ver.ncc, 4),
                           gain=round(ver.gain, 4),
                           offset=round(ver.offset, 4))
        confidence["overlap_ncc"] = ver.ncc
        confidence["overlap_fraction"] = ver.overlap_fraction
        if ver.overlap_fraction < config.min_overlap:
            uncertainties.append(Uncertainty.LOW_OVERLAP)
        if ver.ncc < config.ncc_threshold:
            # Genuinely different content in the overlap: the frequency-domain
            # peak was spurious.  This is how "no real overlap" is told apart
            # from "same content, brightness changed" (which keeps NCC high).
            return _failed(FailureCategory.NO_COMMON_CONTENT, log, confidence)
        if ver.brightness_change:
            uncertainties.append(Uncertainty.BRIGHTNESS_CHANGE)
            brightness = {"gain": ver.gain, "offset": ver.offset,
                          "offset_frac_of_range": ver.offset_frac_of_range,
                          "note": "linear brightness change detected; "
                                  "shift estimate unaffected (NCC is gain/offset invariant)"}

    # ---- 6. border gate ----------------------------------------------------
    with log.timed("border_gate") as step:
        h, w = ref.shape
        limit_y, limit_x = h - config.border_margin, w - config.border_margin
        step.detail.update(valid_range=[limit_y, limit_x])
        if abs(dy) >= limit_y or abs(dx) >= limit_x:
            return _failed(FailureCategory.PEAK_AT_BORDER, log, confidence)

    # A brightness change alone does not make a result uncertain; genuine
    # geometric/ambiguity caveats do.
    non_brightness = [u for u in uncertainties if u is not Uncertainty.BRIGHTNESS_CHANGE]
    status = EstimateStatus.UNCERTAIN if non_brightness else EstimateStatus.OK

    return EstimateResult(
        status=status,
        shift=(dy, dx),
        uncertainties=uncertainties,
        candidates=candidates,
        confidence=confidence,
        brightness=brightness,
        diagnostics={"steps": log.steps},
    )
