"""Layer 2: computation graph.

Pure NumPy kernels with no service state, so they are independently testable
and the dense reference implementation can cross-check them:

* :func:`aggregate_duplicate_indices` - sum contributions sharing a row id,
  divide by the declared reduction scale. Duplicate ids are aggregated
  *before* clipping and the optimizer step.
* :func:`clip_gradients` - declared-mode clipping. ``global`` derives one
  scale from the stacked norm of every touched row; ``row`` derives an
  independent per-row scale. The mode is a required positional argument and
  cannot be selected per row.
* :func:`sgd_step` / :func:`momentum_sgd_step` - sparse optimizer updates.
  Only touched rows are read or written; untouched rows (including their
  momentum buffers) are byte-for-byte unchanged.
* :class:`StepRules` - the zero-gradient / empty-batch step policy, explicit
  and shared by every layer.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sparse_embeddings.config import ClippingConfig
from sparse_embeddings.tensor_types import ErrorCategory, SparseEmbeddingError


@dataclass(frozen=True)
class StepRules:
    """Rules governing which rows consume an optimizer step.

    * An **empty batch** (no tokens at all) performs **no step**: the global
      step counter is not incremented and no row changes.
    * **Untouched rows** never step: neither parameters nor momentum buffers
      are read-modify-written, and per-row step counters stay unchanged.
    * A **touched row whose aggregated gradient is exactly zero** *does*
      consume a step: with momentum SGD its buffer is decayed in place
      (``v <- mu * v``) and its per-row step counter is incremented. This is
      the same semantics a dense implementation produces for that row, so the
      sparse/dense equivalence check below is exact.
    """

    step_on_empty_batch: bool = False
    step_on_zero_touched_gradient: bool = True


def aggregate_duplicate_indices(
    indices: np.ndarray,
    values: np.ndarray,
    *,
    scale: float,
    dim: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Aggregate token contributions into per-row mean gradients.

    Returns ``(unique_indices, aggregated, counts)`` sorted by row id:

    * ``aggregated[i] = sum(values[positions == unique_indices[i]]) / scale``
    * ``counts[i]`` is the number of tokens that hit the row (>= 1).

    Repeated ids are summed *here*, once, before clipping. Using
    ``np.add.at`` would also work; bincount per column is faster and, being
    plain addition of float64 values, is deterministic for a fixed input
    order. An empty input returns empty outputs without error (the caller
    decides the step policy).
    """
    indices = np.asarray(indices, dtype=np.int64)
    values = np.asarray(values, dtype=np.float64)
    if indices.ndim != 1 or values.ndim != 2 or values.shape[1] != dim:
        # Defensive: SparseGradientBatch already validated this.
        raise SparseEmbeddingError(
            ErrorCategory.VALIDATION_ERROR,
            "aggregate_duplicate_indices received malformed arrays",
            details={
                "indices_shape": list(indices.shape),
                "values_shape": list(values.shape),
                "dim": int(dim),
            },
        )
    if indices.shape[0] == 0:
        return (
            np.empty(0, dtype=np.int64),
            np.empty((0, dim), dtype=np.float64),
            np.empty(0, dtype=np.int64),
        )

    max_id = int(indices.max())
    counts = np.bincount(indices, minlength=max_id + 1)
    sums = np.zeros((max_id + 1, dim), dtype=np.float64)
    # Column-wise bincount aggregates every duplicate contribution exactly once.
    for d in range(dim):
        sums[:, d] = np.bincount(indices, weights=values[:, d], minlength=max_id + 1)

    touched = np.nonzero(counts > 0)[0].astype(np.int64)
    aggregated = sums[touched] / scale
    if not np.all(np.isfinite(aggregated)):
        # Inputs were finite; a non-finite result means the reduction itself
        # overflowed. Reject rather than silently propagating Inf.
        raise SparseEmbeddingError(
            ErrorCategory.NUMERIC_ERROR,
            "aggregation produced non-finite gradients (overflow)",
        )
    return touched, aggregated, counts[touched].astype(np.int64)


@dataclass(frozen=True)
class ClipResult:
    gradients: np.ndarray  # clipped per-row gradients, same shape as input
    scales: np.ndarray  # per-row scale actually applied (for logging/audit)
    pre_norm: float  # global norm of the stacked touched rows
    clipped: bool  # whether any scaling happened at all


def clip_gradients(gradients: np.ndarray, clipping: ClippingConfig | None) -> ClipResult:
    """Clip per-row aggregated gradients under the *declared* mode.

    Global and row-wise norms are deliberately computed by separate branches;
    there is no code path that mixes a global threshold with per-row scaling.

    * ``global``: ``g_norm = sqrt(sum_ij g_ij^2)`` over all touched rows;
      one scale ``min(1, max_norm / g_norm)`` multiplies every row.
    * ``row``: one norm and scale per row; rows never interact.
    """
    if gradients.shape[0] == 0:
        return ClipResult(
            gradients=gradients.copy(),
            scales=np.empty(0, dtype=np.float64),
            pre_norm=0.0,
            clipped=False,
        )
    if clipping is None:
        return ClipResult(
            gradients=gradients.copy(),
            scales=np.ones(gradients.shape[0], dtype=np.float64),
            pre_norm=float(np.sqrt(np.sum(gradients * gradients))),
            clipped=False,
        )

    mode = clipping.mode
    max_norm = float(clipping.max_norm)
    if mode == "global":
        return _clip_global(gradients, max_norm)
    if mode == "row":
        return _clip_by_row(gradients, max_norm)
    # Unreachable: ClippingConfig validates mode at construction.
    raise SparseEmbeddingError(
        ErrorCategory.CONFIG_ERROR, f"unsupported clipping mode {mode!r}"
    )


def _clip_global(gradients: np.ndarray, max_norm: float) -> ClipResult:
    pre_norm = float(np.sqrt(np.sum(gradients * gradients)))
    if pre_norm <= max_norm or pre_norm == 0.0:
        return ClipResult(
            gradients=gradients.copy(),
            scales=np.ones(gradients.shape[0], dtype=np.float64),
            pre_norm=pre_norm,
            clipped=False,
        )
    scale = max_norm / pre_norm
    return ClipResult(
        gradients=gradients * scale,
        scales=np.full(gradients.shape[0], scale, dtype=np.float64),
        pre_norm=pre_norm,
        clipped=True,
    )


def _clip_by_row(gradients: np.ndarray, max_norm: float) -> ClipResult:
    row_norms = np.sqrt(np.sum(gradients * gradients, axis=1))
    scales = np.ones_like(row_norms)
    needs = row_norms > max_norm
    # A zero row keeps scale 1 (multiplication would be a no-op anyway).
    safe = np.where(row_norms > 0.0, row_norms, 1.0)
    scales = np.where(needs, max_norm / safe, scales)
    clipped = bool(np.any(needs))
    return ClipResult(
        gradients=gradients * scales[:, None],
        scales=scales,
        pre_norm=float(np.sqrt(np.sum(gradients * gradients))),
        clipped=clipped,
    )


def sgd_step(
    table: np.ndarray,
    unique_indices: np.ndarray,
    gradients: np.ndarray,
    *,
    learning_rate: float,
) -> np.ndarray:
    """Apply plain SGD to touched rows only.

    ``table`` is updated out of place (immutability rule): a copy is returned;
    rows not in ``unique_indices`` share no modification.
    """
    updated = table.copy()
    updated[unique_indices] = table[unique_indices] - learning_rate * gradients
    return updated


def momentum_sgd_step(
    table: np.ndarray,
    momentum_buffer: np.ndarray,
    unique_indices: np.ndarray,
    gradients: np.ndarray,
    *,
    learning_rate: float,
    momentum: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Heavy-ball momentum SGD on touched rows only.

    For each touched row (including zero-gradient touched rows)::

        v_i <- momentum * v_i + g_i
        w_i <- w_i - learning_rate * v_i

    Untouched rows and their buffers are not modified. Updates happen on
    copies.
    """
    updated_table = table.copy()
    updated_buffer = momentum_buffer.copy()
    sel = unique_indices
    updated_buffer[sel] = momentum * momentum_buffer[sel] + gradients
    updated_table[sel] = table[sel] - learning_rate * updated_buffer[sel]
    return updated_table, updated_buffer
