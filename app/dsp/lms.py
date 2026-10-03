"""LMS / NLMS adaptive filter core.

State machine contract (fixed update order, identical for both algorithms):

1. Shift the new reference sample into the tap buffer (index 0 = newest).
2. Compute the filter output  ``y = w . x``  with the *current* weights.
3. Compute the error         ``e = d - y``.
4. Unless adaptation is frozen for this sample, update the weights:
   - LMS : ``w <- w + mu * e * x``
   - NLMS: ``w <- w + mu * e * x / (eps + x . x)``

The output of a sample therefore always reflects the *pre-update* weights.
The NLMS denominator is regularized by a strictly positive ``eps`` so a
silent (all-zero) reference window can never divide by zero; with a zero
buffer the update term is exactly zero and the weights stay untouched.

All state transitions validate finiteness; a non-finite sample or a
non-finite resulting weight vector raises ``ComputationError`` so a
numerical blow-up is distinguishable from bad input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.config import DEFAULT_SETTINGS, Settings
from app.errors import ComputationError, InputValidationError

ALGORITHMS = ("lms", "nlms")


def _validate_scalar(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InputValidationError(f"{name} must be a real number", detail={name: value})
    if not math.isfinite(value):
        raise InputValidationError(
            f"{name} must be finite", detail={name: value}
        )
    return float(value)


@dataclass
class StepResult:
    """Per-sample trace, kept small enough to log for replay."""

    y: float
    e: float
    adapted: bool
    energy: float
    denominator: float


@dataclass
class BlockResult:
    output: np.ndarray
    error: np.ndarray
    adapted_samples: int
    frozen_samples: int
    min_denominator: float
    mean_energy: float
    weight_norm_before: float
    weight_norm_after: float


@dataclass
class AdaptiveFilter:
    """Single-channel LMS/NLMS filter with explicit, inspectable state."""

    filter_len: int
    algorithm: str
    mu: float
    eps: float = DEFAULT_SETTINGS.eps_default
    settings: Settings = DEFAULT_SETTINGS
    weights: np.ndarray = field(init=False)
    buffer: np.ndarray = field(init=False)
    samples_processed: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        s = self.settings
        if self.algorithm not in ALGORITHMS:
            raise InputValidationError(
                f"algorithm must be one of {ALGORITHMS}",
                detail={"algorithm": self.algorithm},
            )
        if not (s.filter_len_min <= self.filter_len <= s.filter_len_max):
            raise InputValidationError(
                "filter_len out of range",
                detail={
                    "filter_len": self.filter_len,
                    "min": s.filter_len_min,
                    "max": s.filter_len_max,
                },
            )
        _validate_scalar("mu", self.mu)
        if not (s.mu_min <= self.mu <= s.mu_max):
            raise InputValidationError(
                "mu outside the allowed learning-rate range",
                detail={"mu": self.mu, "min": s.mu_min, "max": s.mu_max},
            )
        _validate_scalar("eps", self.eps)
        if self.eps < s.eps_min:
            raise InputValidationError(
                "eps too small; NLMS regularization requires eps >= eps_min",
                detail={"eps": self.eps, "eps_min": s.eps_min},
            )
        self.weights = np.zeros(self.filter_len, dtype=np.float64)
        self.buffer = np.zeros(self.filter_len, dtype=np.float64)

    def step(self, x_ref: float, d_primary: float, *, adapt: bool = True) -> StepResult:
        """Advance one sample with the fixed update order documented above."""
        x_ref = _validate_scalar("x_ref", x_ref)
        d_primary = _validate_scalar("d_primary", d_primary)

        # 1. shift reference sample in (index 0 = newest)
        self.buffer[1:] = self.buffer[:-1]
        self.buffer[0] = x_ref

        # 2./3. output and error from pre-update weights
        y = float(self.weights @ self.buffer)
        e = d_primary - y

        with np.errstate(over="ignore", invalid="ignore"):
            energy = float(self.buffer @ self.buffer)
        denominator = 1.0 if self.algorithm == "lms" else self.eps + energy

        # 4. weight update (skipped while frozen). Overflow is checked
        # explicitly right after, so silence the numpy warning here and
        # surface the problem as a categorized ComputationError instead.
        if adapt:
            with np.errstate(over="ignore", invalid="ignore"):
                self.weights = self.weights + (self.mu * e / denominator) * self.buffer
            if not np.all(np.isfinite(self.weights)):
                raise ComputationError(
                    "weight update produced non-finite coefficients",
                    detail={
                        "algorithm": self.algorithm,
                        "mu": self.mu,
                        "energy": energy,
                        "sample_index": self.samples_processed,
                    },
                )

        self.samples_processed += 1
        return StepResult(
            y=y,
            e=e,
            adapted=adapt,
            energy=energy,
            denominator=denominator,
        )

    def process_block(
        self,
        reference: np.ndarray,
        primary: np.ndarray,
        freeze_mask: np.ndarray | None = None,
    ) -> BlockResult:
        """Process a block of samples; ``freeze_mask`` marks frozen samples."""
        reference = np.asarray(reference, dtype=np.float64)
        primary = np.asarray(primary, dtype=np.float64)
        if reference.shape != primary.shape:
            raise InputValidationError(
                "reference and primary must have identical shape",
                detail={"reference": reference.shape, "primary": primary.shape},
            )
        if reference.ndim != 1:
            raise InputValidationError("signals must be 1-D", detail={"ndim": reference.ndim})
        n = reference.shape[0]
        if n == 0:
            raise InputValidationError("block must contain at least one sample")
        if n > self.settings.max_block_samples:
            from app.errors import ResourceExhaustedError

            raise ResourceExhaustedError(
                "block exceeds max_block_samples",
                detail={"samples": n, "max": self.settings.max_block_samples},
            )
        if not (np.all(np.isfinite(reference)) and np.all(np.isfinite(primary))):
            raise InputValidationError("signals must contain only finite values")
        if freeze_mask is None:
            freeze_mask = np.zeros(n, dtype=bool)
        else:
            freeze_mask = np.asarray(freeze_mask, dtype=bool)
            if freeze_mask.shape != (n,):
                raise InputValidationError(
                    "freeze_mask must match block length",
                    detail={"freeze_mask": freeze_mask.shape, "block": n},
                )

        weight_norm_before = float(np.linalg.norm(self.weights))
        output = np.empty(n, dtype=np.float64)
        error = np.empty(n, dtype=np.float64)
        min_denominator = math.inf
        energy_sum = 0.0
        adapted = 0

        for i in range(n):
            result = self.step(float(reference[i]), float(primary[i]), adapt=not freeze_mask[i])
            output[i] = result.y
            error[i] = result.e
            min_denominator = min(min_denominator, result.denominator)
            energy_sum += result.energy
            adapted += int(result.adapted)

        return BlockResult(
            output=output,
            error=error,
            adapted_samples=adapted,
            frozen_samples=n - adapted,
            min_denominator=min_denominator,
            mean_energy=energy_sum / n,
            weight_norm_before=weight_norm_before,
            weight_norm_after=float(np.linalg.norm(self.weights)),
        )


def freeze_mask_from_intervals(n: int, intervals: list[tuple[int, int]]) -> np.ndarray:
    """Build a freeze mask from half-open [start, end) sample intervals."""
    mask = np.zeros(n, dtype=bool)
    last_end = 0
    for start, end in intervals:
        if not (0 <= start < end <= n):
            raise InputValidationError(
                "freeze interval outside block bounds",
                detail={"interval": [start, end], "block": n},
            )
        if start < last_end:
            raise InputValidationError(
                "freeze intervals must be sorted and non-overlapping",
                detail={"interval": [start, end], "previous_end": last_end},
            )
        mask[start:end] = True
        last_end = end
    return mask
