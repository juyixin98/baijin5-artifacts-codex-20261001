"""Application service: orchestrates design, kernel and evidence.

This is the single place where input is validated, statistical work is
dispatched, and every outcome (success, structured failure, approximate
answer) is recorded in the request's evidence record.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings
from .evidence import EvidenceRecord
from .stats.contracts import (
    AcceptanceSetResult,
    ComputationKind,
    FailureCode,
    PValueResult,
    PairDesign,
    TwoSidedMethod,
)
from .stats.design import assignment_differences, iter_assignments, validate_observations
from .stats.estimator import ApproximationUnavailable, RandomizationKernel


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _build_design(payload: Dict[str, Any], evidence: EvidenceRecord) -> Optional[PairDesign]:
    treated = payload.get("treated")
    control = payload.get("control")
    if not isinstance(treated, list) or not isinstance(control, list):
        evidence.fail(
            FailureCode.INVALID_PAIRS.value,
            "'treated' and 'control' must be arrays with one outcome per pair",
            "app.service:_build_design",
            treated_type=type(treated).__name__,
            control_type=type(control).__name__,
        )
        return None
    if not all(_finite_number(v) for v in treated + control):
        evidence.fail(
            FailureCode.INVALID_PAIRS.value,
            "all outcomes must be finite real numbers",
            "app.service:_build_design",
        )
        return None
    try:
        design = validate_observations(treated, control)
    except ValueError as exc:
        code = FailureCode.TOO_FEW_PAIRS if "at least" in str(exc) else FailureCode.INVALID_PAIRS
        evidence.fail(code.value, str(exc), "app.service:_build_design")
        return None
    evidence.step(
        "build_design",
        f"validated strictly-paired design with {design.n_pairs} pairs "
        f"(randomization set size {design.randomization_set_size})",
        "app.service:_build_design",
        n_pairs=design.n_pairs,
        randomization_set_size=design.randomization_set_size,
        differences=list(design.differences),
    )
    return design


def _parse_method(raw: Any, evidence: EvidenceRecord) -> Optional[TwoSidedMethod]:
    try:
        return TwoSidedMethod(raw)
    except ValueError:
        allowed = [m.value for m in TwoSidedMethod]
        evidence.fail(
            FailureCode.INVALID_METHOD.value,
            f"unknown two-sided method {raw!r}; expected one of {allowed}",
            "app.service:_parse_method",
            allowed=allowed,
        )
        return None


def _parse_alpha(raw: Any, evidence: EvidenceRecord, *, required: bool) -> Optional[float]:
    if raw is None:
        if required:
            evidence.fail(
                FailureCode.INVALID_ALPHA.value,
                "'alpha' is required",
                "app.service:_parse_alpha",
            )
        return None
    if not _finite_number(raw) or not 0.0 < float(raw) < 1.0:
        evidence.fail(
            FailureCode.INVALID_ALPHA.value,
            f"alpha must lie strictly in (0, 1), got {raw!r}",
            "app.service:_parse_alpha",
        )
        return None
    return float(raw)


def _p_value_to_dict(r: PValueResult) -> Dict[str, Any]:
    return asdict(r) | {"method": r.method.value, "kind": r.kind.value}


def _set_to_dict(r: AcceptanceSetResult) -> Dict[str, Any]:
    return {
        "method": r.method.value,
        "alpha": r.alpha,
        "kind": r.kind.value,
        "certified": r.certified,
        "is_disconnected": r.is_disconnected,
        "is_empty": r.is_empty,
        "n_components": len(r.components),
        "components": [asdict(c) | {"rendered": c.render()} for c in r.components],
        "hull": None if r.hull is None else asdict(r.hull) | {"rendered": r.hull.render()},
        "n_pairs": r.n_pairs,
        "randomization_set_size": r.randomization_set_size,
        "boundary_tolerance": r.boundary_tolerance,
        "standard_error": r.standard_error,
        "n_p_evaluations": r.n_p_evaluations,
        "uncertainty": list(r.uncertainty),
    }


class InferenceService:
    def __init__(self, settings: Settings):
        self._settings = settings

    def p_value(self, payload: Dict[str, Any], evidence: EvidenceRecord) -> Optional[Dict[str, Any]]:
        design = _build_design(payload, evidence)
        if design is None:
            return None
        method = _parse_method(payload.get("method", TwoSidedMethod.ABS.value), evidence)
        if method is None:
            return None
        tau = payload.get("tau", 0.0)
        if not _finite_number(tau):
            evidence.fail(
                FailureCode.INVALID_TAU.value,
                f"tau must be a finite number, got {tau!r}",
                "app.service:p_value",
            )
            return None
        alpha = _parse_alpha(payload.get("alpha"), evidence, required=False)
        if payload.get("alpha") is not None and alpha is None:
            return None

        kernel = RandomizationKernel(design.differences, method, self._settings)
        over_budget = kernel.randomization_set_size > self._settings.exact_budget
        evidence.step(
            "select_strategy",
            ("Monte Carlo approximation" if over_budget else "exact full enumeration")
            + f" for randomization set of size {kernel.randomization_set_size}",
            "app.service:p_value",
            exact_budget=self._settings.exact_budget,
            over_budget=over_budget,
        )
        try:
            result = kernel.p_value(float(tau), alpha)
        except ApproximationUnavailable as exc:
            evidence.fail(
                FailureCode.APPROXIMATION_UNRESOLVED.value,
                str(exc),
                "app.service:p_value",
                randomization_set_size=kernel.randomization_set_size,
                exact_budget=self._settings.exact_budget,
                suggested_method=TwoSidedMethod.ABS.value,
            )
            return None
        evidence.step(
            "compute_p_value",
            f"{result.kind.value} two-sided p={result.p_value:.6g} under {method.value}",
            "app.service:p_value",
            p_value=result.p_value,
            kind=result.kind.value,
            monte_carlo_error=result.monte_carlo_error,
        )
        for text in result.uncertainty:
            evidence.note_uncertainty(text)
        return _p_value_to_dict(result)

    def invert(self, payload: Dict[str, Any], evidence: EvidenceRecord) -> Optional[Dict[str, Any]]:
        design = _build_design(payload, evidence)
        if design is None:
            return None
        method = _parse_method(payload.get("method", TwoSidedMethod.ABS.value), evidence)
        if method is None:
            return None
        alpha = _parse_alpha(payload.get("alpha"), evidence, required=True)
        if alpha is None:
            return None

        kernel = RandomizationKernel(design.differences, method, self._settings)
        size = kernel.randomization_set_size
        crossings = size * (size - 1) // 2
        prob_feasible = method is TwoSidedMethod.ABS or crossings <= self._settings.crossing_budget
        over_budget = size > self._settings.exact_budget or not prob_feasible
        evidence.step(
            "select_strategy",
            ("approximate grid inversion" if over_budget else "exact certified breakpoint inversion")
            + f" (set size {size}, pair crossings {crossings})",
            "app.service:invert",
            exact_budget=self._settings.exact_budget,
            crossing_budget=self._settings.crossing_budget,
            over_budget=over_budget,
        )
        try:
            result = kernel.invert(alpha)
        except ApproximationUnavailable as exc:
            evidence.fail(
                FailureCode.APPROXIMATION_UNRESOLVED.value,
                str(exc),
                "app.service:invert",
                randomization_set_size=size,
                exact_budget=self._settings.exact_budget,
                suggested_method=TwoSidedMethod.ABS.value,
            )
            return None
        for text in result.uncertainty:
            evidence.note_uncertainty(text)
        evidence.step(
            "invert_constant_effect",
            f"{result.kind.value} acceptance set at alpha={alpha} has "
            f"{len(result.components)} component(s)"
            + (" (DISCONNECTED)" if result.is_disconnected else "")
            + (" [not certified exhaustive]" if not result.certified else ""),
            "app.service:invert",
            kind=result.kind.value,
            certified=result.certified,
            n_components=len(result.components),
            disconnected=result.is_disconnected,
            components=[c.render() for c in result.components],
        )
        return _set_to_dict(result)

    def randomization_preview(self, payload: Dict[str, Any], evidence: EvidenceRecord) -> Optional[Dict[str, Any]]:
        design = _build_design(payload, evidence)
        if design is None:
            return None
        preview_limit = int(payload.get("preview_limit", 16))
        signed = assignment_differences(design)
        totals = signed.sum(axis=1)
        assignments: List[Dict[str, Any]] = []
        for idx, (nt, nc) in enumerate(iter_assignments(design)):
            if idx >= preview_limit:
                break
            assignments.append(
                {
                    "index": idx,
                    "flipped_pairs": [i for i in range(design.n_pairs) if (idx >> i) & 1],
                    "treated": list(nt),
                    "control": list(nc),
                    "statistic_at_tau_zero": float(totals[idx]),
                }
            )
        evidence.step(
            "preview_randomization_set",
            f"enumerated {design.randomization_set_size} strictly-paired assignments "
            f"(showing first {len(assignments)})",
            "app.service:randomization_preview",
            set_size=design.randomization_set_size,
            shown=len(assignments),
        )
        return {
            "n_pairs": design.n_pairs,
            "randomization_set_size": design.randomization_set_size,
            "design_principle": "one independent flip per pair; outcomes never cross pair boundaries",
            "assignments": assignments,
        }
