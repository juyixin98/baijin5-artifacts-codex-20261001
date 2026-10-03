"""Request pipeline: validate -> stretch -> diagnostic verdict.

Shared by the HTTP API and the verification script so both report identical
decisions and notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .contracts import Decision, ValidatedRequest, validate_request
from .diagnostics import log_decision, redact_label
from .wsola import StretchResult, WsolaParams, make_params, wsola_stretch

# Peak amplitude below which the whole input is treated as silence: the
# correlation search carries no signal-derived information.
SILENCE_AMPLITUDE_EPS = 1e-8


@dataclass
class StretchOutcome:
    validated: ValidatedRequest
    result: StretchResult
    decision: Decision
    notes: list[str] = field(default_factory=list)


def run_stretch(
    samples: np.ndarray,
    sample_rate: int,
    time_scale: float,
    *,
    request_id: str,
    input_label: str | None = None,
) -> StretchOutcome:
    """Validate and stretch, logging the verdict with key state.

    Raises ContractViolation (after logging the rejection) on bad input.
    """
    params: WsolaParams = make_params(sample_rate if isinstance(sample_rate, int) else 0, time_scale if isinstance(time_scale, (int, float)) else 0.0)
    log_state = dict(
        input_label=redact_label(input_label),
        sample_rate=sample_rate,
        time_scale=time_scale,
        n_samples=int(samples.shape[0]) if samples.ndim == 1 else None,
    )
    try:
        validated = validate_request(sample_rate, time_scale, samples, params.window_len)
    except Exception as exc:
        log_decision(request_id, Decision.REJECTED, getattr(exc, "message", str(exc)), **log_state)
        raise

    result = wsola_stretch(samples, params)
    notes: list[str] = []
    tie_frames = sum(1 for f in result.frames if f.tied_candidates > 1)
    if tie_frames:
        notes.append(f"tie_break_frames={tie_frames} (deterministic: min |offset|, then smaller offset)")
    clamped = sum(1 for f in result.frames if f.natural_pos != f.index * params.analysis_hop)
    if clamped:
        notes.append(f"end_clamped_frames={clamped} (analysis position clamped to input tail)")

    peak = float(np.max(np.abs(samples)))
    if peak < SILENCE_AMPLITUDE_EPS:
        decision = Decision.UNDECIDABLE
        notes.append("input is silence; offsets are the deterministic default (0), not matches")
        reason = "silence: correlation undefined on every frame"
    elif notes:
        decision = Decision.ACCEPTED_WITH_NOTES
        reason = "stretched with notes"
    else:
        decision = Decision.ACCEPTED
        reason = "stretched within supported range"

    log_decision(
        request_id,
        decision,
        reason,
        **log_state,
        target_length=result.target_length,
        n_frames=len(result.frames),
        tie_break_frames=tie_frames,
        end_clamped_frames=clamped,
    )
    return StretchOutcome(validated=validated, result=result, decision=decision, notes=notes)
