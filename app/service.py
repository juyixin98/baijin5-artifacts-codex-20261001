"""Service layer: validation policy + engine dispatch + diagnostics.

Sits between the HTTP boundary (api.py) and the numerical kernels.
Owns the accept/reject/clip decision and always produces a Diagnostics
record explaining that decision.
"""

from __future__ import annotations

import numpy as np

from .config import Settings
from .diagnostics import log_diagnostics, new_request_id
from .imaging import PairValidation, content_hash_prefix, validate_pair
from .kernel.queue_impl import KernelStats, reconstruct_queue
from .kernel.reference import reconstruct_reference
from .schemas import (
    Connectivity,
    Diagnostics,
    Engine,
    FailureCategory,
    JobStatus,
    ViolationPolicy,
)
from .tiling import reconstruct_tiled


class ReconstructionRejected(Exception):
    """Raised when inputs fail validation under the active policy."""

    def __init__(self, diagnostics: Diagnostics):
        super().__init__("; ".join(diagnostics.reasons))
        self.diagnostics = diagnostics


def _base_diagnostics(
    request_id: str,
    marker: np.ndarray | None,
    mask: np.ndarray | None,
    connectivity: Connectivity,
    engine: Engine,
    on_violation: ViolationPolicy,
) -> Diagnostics:
    diag = Diagnostics(
        request_id=request_id,
        status=JobStatus.UNDETERMINED,
        connectivity=int(connectivity),
        engine=engine.value,
        on_violation=on_violation.value,
    )
    if marker is not None:
        diag.shape = list(marker.shape)
        diag.dtype = str(marker.dtype)
        diag.marker_sha256_12 = content_hash_prefix(marker)
    if mask is not None:
        diag.mask_sha256_12 = content_hash_prefix(mask)
    return diag


def _run_engine(
    engine: Engine,
    marker: np.ndarray,
    mask: np.ndarray,
    connectivity: Connectivity,
    settings: Settings,
) -> tuple[np.ndarray, KernelStats]:
    if engine is Engine.QUEUE:
        return reconstruct_queue(marker, mask, connectivity)
    if engine is Engine.REFERENCE:
        return reconstruct_reference(
            marker,
            mask,
            connectivity,
            max_iterations=settings.max_reference_iterations,
        )
    if engine is Engine.TILED:
        return reconstruct_tiled(
            marker, mask, connectivity, tile_size=settings.tile_size
        )
    raise ValueError(f"unknown engine: {engine}")


def validate_only(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    connectivity: Connectivity,
    on_violation: ViolationPolicy,
    settings: Settings,
    request_id: str | None = None,
) -> Diagnostics:
    """Run the contract checks without reconstructing (for /validate)."""
    request_id = request_id or new_request_id()
    diag = _base_diagnostics(
        request_id, marker, mask, connectivity, Engine.QUEUE, on_violation
    )
    diag.engine = None
    outcome = validate_pair(marker, mask, max_side=settings.max_image_side)
    _apply_validation_outcome(diag, outcome, on_violation)
    log_diagnostics(diag)
    return diag


def _apply_validation_outcome(
    diag: Diagnostics,
    outcome: PairValidation,
    on_violation: ViolationPolicy,
) -> None:
    if outcome.ok:
        diag.status = JobStatus.ACCEPTED
        diag.reasons.append("inputs satisfy the marker<=mask contract")
        return
    diag.violation_pixels = outcome.violation_pixels
    diag.reasons.extend(outcome.reasons)
    if (
        outcome.failure_category is FailureCategory.MARKER_EXCEEDS_MASK
        and on_violation is ViolationPolicy.CLIP
    ):
        diag.status = JobStatus.CLIPPED
        diag.reasons.append("policy=clip: marker clipped to min(marker, mask)")
    else:
        diag.status = JobStatus.REJECTED
        diag.failure_category = outcome.failure_category


def reconstruct(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    connectivity: Connectivity,
    engine: Engine,
    on_violation: ViolationPolicy,
    settings: Settings,
    request_id: str | None = None,
) -> tuple[np.ndarray, Diagnostics]:
    """Validate, then reconstruct; raises ReconstructionRejected on
    invalid input (unless the violation is clip-able and policy=clip)."""
    request_id = request_id or new_request_id()
    diag = _base_diagnostics(
        request_id, marker, mask, connectivity, engine, on_violation
    )

    outcome = validate_pair(marker, mask, max_side=settings.max_image_side)
    _apply_validation_outcome(diag, outcome, on_violation)
    if diag.status is JobStatus.REJECTED:
        log_diagnostics(diag)
        raise ReconstructionRejected(diag)

    effective_marker = marker
    if diag.status is JobStatus.CLIPPED:
        # Explicit, recorded clipping — never silent.
        effective_marker = np.minimum(marker, mask)

    try:
        result, stats = _run_engine(
            engine, effective_marker, mask, connectivity, settings
        )
    except RuntimeError as exc:
        diag.status = JobStatus.UNDETERMINED
        diag.failure_category = FailureCategory.ITERATION_LIMIT
        diag.reasons.append(str(exc))
        log_diagnostics(diag)
        raise ReconstructionRejected(diag) from exc

    diag.iterations = stats.iterations
    diag.queue_pops = stats.queue_pops
    diag.changed_pixels = stats.changed_pixels
    log_diagnostics(diag)
    return result, diag
