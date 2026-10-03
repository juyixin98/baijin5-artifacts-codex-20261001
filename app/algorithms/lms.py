"""Core LMS / NLMS adaptive filters.

Numerical contract (fixed, do not reorder):

  Buffer convention: ``buffer`` holds the last ``L`` reference samples,
  oldest first, newest last. The coefficient vector is stored in the same
  order, so relative to a convolution kernel in ``scipy.signal.lfilter``
  convention the learned coefficients are the *time-reversed* impulse
  response: ``weights[L-1-k]`` multiplies ``x(n-k)``.

  On each sample ``n``:

    1. shift ``x(n)`` into the buffer (drop oldest, append newest)
    2. ``y(n) = w . buffer``                      (filter output)
    3. ``e(n) = d(n) - y(n)``                     (error, pre-update)
    4. unless the sample is frozen:
         LMS : ``w <- w + mu * e(n) * buffer``
         NLMS: ``w <- w + mu * e(n) * buffer / (epsilon + buffer.buffer)``

  The error is always computed from the *pre-update* weights, and the buffer
  is never modified after the weight update within the same step.

Learning-rate bounds (enforced at construction, ``input_validation`` on
violation): LMS ``0 < mu <= settings.mu_max_lms``; NLMS ``0 < mu < 2``.
The NLMS energy regularisation ``epsilon`` guarantees the denominator is
strictly positive, so a silent (all-zero) reference cannot divide by zero.

Block processing is atomic: the filter works on copies of its state and
commits only when every output and weight is finite; otherwise a
``ComputationError`` is raised and the prior state is preserved, so the
caller may retry the same sample index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.config import Settings
from app.errors import ComputationError, InputValidationError

Algorithm = Literal["lms", "nlms"]


@dataclass(frozen=True)
class FilterSpec:
    algorithm: Algorithm
    filter_length: int
    mu: float
    epsilon: float = 1e-8


@dataclass(frozen=True)
class BlockResult:
    outputs: np.ndarray
    errors: np.ndarray
    weight_norm: float
    samples_processed: int
    frozen_samples: int


def validate_spec(spec: FilterSpec, settings: Settings) -> None:
    if not 1 <= spec.filter_length <= settings.max_filter_length:
        raise InputValidationError(
            "filter_length out of range",
            reason="filter_length_range",
            detail={"filter_length": spec.filter_length,
                    "max": settings.max_filter_length},
        )
    if spec.algorithm == "lms":
        if not 0.0 < spec.mu <= settings.mu_max_lms:
            raise InputValidationError(
                "LMS step size out of range",
                reason="mu_range",
                detail={"mu": spec.mu, "lower": 0.0,
                        "upper": settings.mu_max_lms, "upper_inclusive": True},
            )
    else:  # nlms
        if not 0.0 < spec.mu < settings.nlms_mu_upper:
            raise InputValidationError(
                "NLMS step size must satisfy 0 < mu < 2",
                reason="mu_range",
                detail={"mu": spec.mu, "lower": 0.0,
                        "upper": settings.nlms_mu_upper,
                        "upper_inclusive": False},
            )
    if not spec.epsilon > 0.0:
        raise InputValidationError(
            "epsilon must be positive",
            reason="epsilon_range",
            detail={"epsilon": spec.epsilon},
        )


def _checked_block_inputs(
    reference: np.ndarray,
    desired: np.ndarray,
    freeze_mask: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Boundary validation for a block; returns normalised arrays."""
    reference = np.asarray(reference, dtype=np.float64)
    desired = np.asarray(desired, dtype=np.float64)
    if reference.shape != desired.shape:
        raise InputValidationError(
            "reference and desired must have equal length",
            reason="length_mismatch",
            detail={"reference": reference.shape[0], "desired": desired.shape[0]},
        )
    if reference.ndim != 1 or reference.size == 0:
        raise InputValidationError(
            "blocks must be non-empty 1-D arrays",
            reason="empty_block",
        )
    if not (np.all(np.isfinite(reference)) and np.all(np.isfinite(desired))):
        raise InputValidationError(
            "block contains NaN or infinite samples",
            reason="non_finite_input",
        )
    if freeze_mask is None:
        return reference, desired, np.zeros(reference.size, dtype=bool)
    if freeze_mask.shape != reference.shape:
        raise InputValidationError(
            "freeze_mask length must match the block",
            reason="length_mismatch",
            detail={"freeze_mask": freeze_mask.shape[0], "block": reference.size},
        )
    return reference, desired, freeze_mask


class AdaptiveFilter:
    """Single-channel LMS/NLMS filter with explicit, inspectable state."""

    def __init__(self, spec: FilterSpec, settings: Settings) -> None:
        validate_spec(spec, settings)
        self._spec = spec
        self._w = np.zeros(spec.filter_length, dtype=np.float64)
        self._buf = np.zeros(spec.filter_length, dtype=np.float64)
        self._index = 0  # next sample index to consume

    # -- introspection ----------------------------------------------------

    @property
    def spec(self) -> FilterSpec:
        return self._spec

    @property
    def next_index(self) -> int:
        return self._index

    def weights(self) -> np.ndarray:
        return self._w.copy()

    def buffer(self) -> np.ndarray:
        return self._buf.copy()

    # -- single sample -----------------------------------------------------

    def step(self, x: float, d: float, freeze: bool = False) -> tuple[float, float]:
        """Advance one sample following the fixed update order in the module
        docstring. Returns ``(y, e)``; weights change only when not frozen."""
        self._buf[:-1] = self._buf[1:]
        self._buf[-1] = x
        y = float(self._w @ self._buf)
        e = float(d) - y
        if not freeze:
            if self._spec.algorithm == "lms":
                gain = self._spec.mu
            else:
                gain = self._spec.mu / (self._spec.epsilon + float(self._buf @ self._buf))
            self._w = self._w + gain * e * self._buf
        self._index += 1
        return y, e

    # -- block -------------------------------------------------------------

    def process_block(
        self,
        reference: np.ndarray,
        desired: np.ndarray,
        freeze_mask: np.ndarray | None = None,
    ) -> BlockResult:
        """Process a block atomically.

        ``freeze_mask[i] == True`` means sample ``i`` is filtered but the
        weights are not updated for it (frozen adaptation interval).
        On any non-finite result the state is left untouched and a
        ``ComputationError`` is raised.
        """
        reference, desired, freeze_mask = _checked_block_inputs(
            reference, desired, freeze_mask
        )
        return self._run_atomic(reference, desired, freeze_mask)

    def _run_atomic(
        self,
        reference: np.ndarray,
        desired: np.ndarray,
        freeze_mask: np.ndarray,
    ) -> BlockResult:
        """Run the block on state copies; commit only if fully finite."""
        w_saved, buf_saved, idx_saved = self._w, self._buf, self._index
        self._w = self._w.copy()
        self._buf = self._buf.copy()

        outputs = np.empty(reference.size, dtype=np.float64)
        errors = np.empty(reference.size, dtype=np.float64)
        try:
            # Overflow/invalid warnings are expected on diverging runs and
            # are handled by the explicit finiteness check below.
            with np.errstate(over="ignore", invalid="ignore"):
                for i in range(reference.size):
                    outputs[i], errors[i] = self.step(
                        reference[i], desired[i], freeze=bool(freeze_mask[i])
                    )
        except BaseException:
            self._w, self._buf, self._index = w_saved, buf_saved, idx_saved
            raise
        if not (
            np.all(np.isfinite(self._w))
            and np.all(np.isfinite(outputs))
            and np.all(np.isfinite(errors))
        ):
            # Roll back: the caller can retry the same index.
            self._w, self._buf, self._index = w_saved, buf_saved, idx_saved
            raise ComputationError(
                "non-finite state or output during block processing; "
                "filter state rolled back",
                reason="non_finite_state",
                detail={"block_start_index": int(idx_saved),
                        "block_length": int(reference.size)},
            )
        return BlockResult(
            outputs=outputs,
            errors=errors,
            weight_norm=float(np.linalg.norm(self._w)),
            samples_processed=int(reference.size),
            frozen_samples=int(np.count_nonzero(freeze_mask)),
        )
