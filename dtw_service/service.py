"""Service orchestration: validate, align, diagnose.

Pipeline for one request:

1. Boundary validation (empty, non-finite, length limit, window sanity).
   Each failure maps to exactly one ``FailureCategory``.
2. Endpoint reachability pre-check (``|n - m| <= radius``) — a too-narrow
   window fails fast as ``WINDOW_TOO_NARROW`` instead of running the DP.
3. Banded DTW; an infinite endpoint cost means ``UNREACHABLE_ENDPOINT``.
4. Degeneracy check: if every local distance inside the band is zero, all
   legal paths are equally optimal — the alignment is reported as
   ``INDETERMINATE`` (a path is still returned, flagged non-unique).
5. Normalized cost uses the fixed denominator ``n + m`` (consumed samples),
   which is identical for every legal path of a given pair.

Every attempt emits a ``DecisionRecord`` with the request id and masked
sequence fingerprints; raw feature values never reach logs or diagnostics.
"""

from __future__ import annotations

import math
import uuid

import numpy as np

from dtw_service.banded import banded_dtw
from dtw_service.constraints import DEFAULT_STEP_PATTERN, SakoeChibaWindow
from dtw_service.contracts import (
    AlignmentRequest,
    AlignmentResponse,
    DecisionStatus,
    FailureCategory,
    FailureInfo,
)
from dtw_service.diagnostics import DecisionRecord, log_decision, mask_sequence
from dtw_service.distance import METRIC_NAME, band_max_distance
from dtw_service.settings import Settings, load_settings
from dtw_service.stretch import smooth_stretch, step_stretches


def _reject(
    request_id: str,
    category: FailureCategory,
    message: str,
    key_state: dict,
) -> AlignmentResponse:
    record = DecisionRecord(request_id, DecisionStatus.REJECTED, message, key_state)
    log_decision(record)
    return AlignmentResponse(
        request_id=request_id,
        status=DecisionStatus.REJECTED,
        failure=FailureInfo(category=category, message=message),
        diagnostics=record.as_dict(),
    )


def align(
    request: AlignmentRequest,
    settings: Settings | None = None,
) -> AlignmentResponse:
    """Align two scalar feature sequences and return path, cost, stretch."""
    settings = settings or load_settings()
    request_id = request.record_id or uuid.uuid4().hex[:12]
    a = np.asarray(request.sequence_a, dtype=float)
    b = np.asarray(request.sequence_b, dtype=float)
    n, m = len(a), len(b)
    key_state = {
        "sequence_a": mask_sequence(request.sequence_a),
        "sequence_b": mask_sequence(request.sequence_b),
        "metric": METRIC_NAME,
        "step_pattern": [list(s) for s in DEFAULT_STEP_PATTERN.steps],
    }

    if n == 0 or m == 0:
        return _reject(request_id, FailureCategory.EMPTY_SEQUENCE,
                       f"empty sequence (len_a={n}, len_b={m})", key_state)
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        return _reject(request_id, FailureCategory.NON_FINITE_VALUES,
                       "sequences must contain only finite values", key_state)
    if n > settings.max_sequence_length or m > settings.max_sequence_length:
        return _reject(request_id, FailureCategory.LENGTH_LIMIT_EXCEEDED,
                       f"length limit {settings.max_sequence_length} exceeded",
                       key_state)

    radius = request.window_radius if request.window_radius is not None \
        else settings.sakoe_chiba_radius
    key_state["window_radius"] = radius
    if radius < 0:
        return _reject(request_id, FailureCategory.INVALID_WINDOW,
                       f"window radius must be >= 0, got {radius}", key_state)

    window = SakoeChibaWindow(radius)
    if not window.endpoint_reachable(n, m):
        return _reject(
            request_id, FailureCategory.WINDOW_TOO_NARROW,
            f"|len_a - len_b| = {abs(n - m)} exceeds window radius {radius}; "
            "endpoint can never be reached inside the band",
            key_state,
        )

    result = banded_dtw(a, b, window)
    if not math.isfinite(result.cost) or result.path is None:
        return _reject(request_id, FailureCategory.UNREACHABLE_ENDPOINT,
                       "no legal path reaches the endpoint inside the window",
                       key_state)

    stretches = step_stretches(result.path)
    normalized = result.cost / (n + m)
    key_state.update({
        "cost": result.cost,
        "normalized_cost": normalized,
        "path_length": len(result.path),
        "normalization_denominator": n + m,
    })

    if band_max_distance(a, b, radius) == 0.0:
        reason = ("all local distances inside the band are zero; every legal "
                  "path is equally optimal, alignment is not identifiable")
        record = DecisionRecord(request_id, DecisionStatus.INDETERMINATE,
                                reason, key_state)
        log_decision(record)
        status = DecisionStatus.INDETERMINATE
    else:
        reason = "alignment computed under fixed metric/step/window contract"
        record = DecisionRecord(request_id, DecisionStatus.ACCEPTED,
                                reason, key_state)
        log_decision(record)
        status = DecisionStatus.ACCEPTED

    return AlignmentResponse(
        request_id=request_id,
        status=status,
        path=[[i, j] for i, j in result.path],
        cost=result.cost,
        normalized_cost=normalized,
        stretch=stretches,
        stretch_smoothed=smooth_stretch(stretches, settings.smoothing_window),
        diagnostics=record.as_dict(),
    )
