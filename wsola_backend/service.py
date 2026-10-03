"""Service layer: validation, decision logging, orchestration.

Accept / reject / undecidable decisions are all recorded with the request
id and the key state that motivated them:

* reject  RATE_OUT_OF_RANGE   rate outside the hard limits [0.25, 4.0]
* reject  INPUT_TOO_SHORT     fewer samples than one analysis window
* accept+warning  RATE_OUTSIDE_QUALITY_RANGE  inside hard limits but
          outside the guaranteed-quality range [0.5, 2.0]
* undecidable->rule  DEGENERATE_MATCH  silence/DC made the match score
          undefined; the deterministic tie-break rule picked the offset
"""
from __future__ import annotations

import numpy as np

from .config import (
    HARD_MAX_RATE,
    HARD_MIN_RATE,
    QUALITY_MAX_RATE,
    QUALITY_MIN_RATE,
)
from .contracts import (
    DiagnosticRecord,
    SegmentRecord,
    StretchStats,
    TimeStretchRequest,
    TimeStretchResponse,
)
from .diagnostics import DecisionLog, content_fingerprint, new_request_id
from .wsola import InputTooShortError, wsola_stretch


class RejectionError(Exception):
    """Domain rejection; the API layer maps it to HTTP 422."""

    def __init__(self, request_id: str, log: DecisionLog, record: DiagnosticRecord):
        self.request_id = request_id
        self.log = log
        self.record = record
        super().__init__(f"{record.code}: {record.message}")


def _to_record(diagnostic) -> DiagnosticRecord:
    return DiagnosticRecord(**diagnostic.as_dict())


def run_time_stretch(request: TimeStretchRequest) -> TimeStretchResponse:
    request_id = request.request_id or new_request_id()
    log = DecisionLog(request_id=request_id)
    x = np.asarray(request.samples, dtype=np.float64)
    n = int(x.shape[0])
    fingerprint = content_fingerprint(x)

    # Masked state only: length + fingerprint, never sample values.
    log.info(
        "REQUEST_RECEIVED",
        "time-stretch request received",
        input_length=n,
        input_fingerprint=fingerprint,
        sample_rate=request.sample_rate,
        rate=request.rate,
    )

    if not (HARD_MIN_RATE <= request.rate <= HARD_MAX_RATE):
        record = log.warning(
            "RATE_OUT_OF_RANGE",
            f"rate {request.rate} outside hard limits "
            f"[{HARD_MIN_RATE}, {HARD_MAX_RATE}]; request rejected",
            rate=request.rate,
            hard_min=HARD_MIN_RATE,
            hard_max=HARD_MAX_RATE,
        )
        raise RejectionError(request_id, log, _to_record(record))
    if not (QUALITY_MIN_RATE <= request.rate <= QUALITY_MAX_RATE):
        log.warning(
            "RATE_OUTSIDE_QUALITY_RANGE",
            f"rate {request.rate} accepted but outside the guaranteed-quality "
            f"range [{QUALITY_MIN_RATE}, {QUALITY_MAX_RATE}]; artifacts expected",
            rate=request.rate,
            quality_min=QUALITY_MIN_RATE,
            quality_max=QUALITY_MAX_RATE,
        )

    try:
        result = wsola_stretch(x, request.rate)
    except InputTooShortError as exc:
        record = log.warning(
            "INPUT_TOO_SHORT",
            f"input length {exc.n} below minimum {exc.minimum}; request rejected",
            input_length=exc.n,
            minimum=exc.minimum,
        )
        raise RejectionError(request_id, log, _to_record(record)) from exc

    degenerate_count = sum(1 for s in result.segments if s.degenerate)
    if degenerate_count:
        log.info(
            "DEGENERATE_MATCH",
            "match score undecidable (silence/DC) for some segments; "
            "offsets chosen by the deterministic tie-break rule",
            degenerate_segments=degenerate_count,
            total_segments=len(result.segments),
        )
    pinned_count = sum(1 for s in result.segments if s.pinned)
    if pinned_count:
        log.info(
            "TAIL_PINNED",
            "input exhausted before the ideal analysis trajectory ended; "
            "final segments pinned to the last placeable frame start",
            pinned_segments=pinned_count,
            total_segments=len(result.segments),
        )
    log.info(
        "END_COMPENSATION",
        f"end rule {result.end_rule} applied",
        end_rule=result.end_rule,
        end_compensation_samples=result.end_compensation_samples,
        target_length=result.target_length,
        frames_planned=result.frames_planned,
        frames_placed=result.frames_placed,
    )
    log.info(
        "REQUEST_ACCEPTED",
        "time-stretch completed",
        output_length=int(result.samples.shape[0]),
        max_position_drift=result.max_position_drift,
    )

    return TimeStretchResponse(
        request_id=request_id,
        sample_rate=request.sample_rate,
        rate=request.rate,
        samples=result.samples.tolist(),
        segments=[SegmentRecord(**vars(s)) for s in result.segments],
        stats=StretchStats(
            input_length=n,
            target_length=result.target_length,
            output_length=int(result.samples.shape[0]),
            frames_planned=result.frames_planned,
            frames_placed=result.frames_placed,
            end_rule=result.end_rule,
            end_compensation_samples=result.end_compensation_samples,
            max_position_drift=result.max_position_drift,
        ),
        diagnostics=[_to_record(d) for d in log.records],
    )
