"""Sample and parameter contracts.

This module is the single place that decides what a valid estimation
request looks like. Both the one-shot API path and the streaming path
validate through these functions, so the data/error contract between
modules stays identical regardless of entry point.

Contract rules (all violations raise InputValidationError unless noted):

- excitation and response are 1-D, equal length, finite (no NaN/Inf)
- model_order is an integer in [1, MAX_MODEL_ORDER]
- number of samples must be >= model_order + 1 (need at least one
  degree of freedom to fit and one to say anything about the fit)
- delay is an integer with |delay| < n_samples
- regularization (ridge lambda) is finite and >= 0
- holdout_fraction is in [0, 0.9]
- n_samples must not exceed the configured limit (ResourceExhaustedError)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .errors import InputValidationError, ResourceExhaustedError

DEFAULT_MAX_SAMPLES = 200_000
MAX_MODEL_ORDER = 4_096
MAX_HOLDOUT_FRACTION = 0.9


@dataclass(frozen=True)
class SampleBlock:
    """A time-aligned pair of excitation/response sample vectors."""

    excitation: np.ndarray
    response: np.ndarray

    @property
    def n_samples(self) -> int:
        return int(self.excitation.shape[0])


@dataclass(frozen=True)
class EstimateParams:
    """Explicit estimation parameters.

    model_order: number of FIR taps L (explicit, never inferred).
    delay: integer sample offset of the response relative to the
        excitation. Positive means the response lags the excitation.
        Alignment is explicit: the caller states it, we apply it.
    regularization: ridge lambda >= 0.
    holdout_fraction: fraction of the (aligned) tail reserved for
        out-of-sample prediction error. 0 disables the holdout.
    """

    model_order: int
    delay: int = 0
    regularization: float = 0.0
    holdout_fraction: float = 0.25


@dataclass(frozen=True)
class EstimateRequest:
    """Validated estimation request: samples plus explicit parameters."""

    samples: SampleBlock
    params: EstimateParams
    max_samples: int = field(default=DEFAULT_MAX_SAMPLES, compare=False)


def _as_1d_float_array(values: object, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise InputValidationError(
            f"{name} must be a 1-D sequence, got shape {arr.shape}",
            detail={"field": name, "shape": list(arr.shape)},
        )
    if arr.size == 0:
        raise InputValidationError(
            f"{name} must not be empty", detail={"field": name}
        )
    if not np.all(np.isfinite(arr)):
        raise InputValidationError(
            f"{name} contains NaN or infinite values",
            detail={"field": name},
        )
    return arr


def validate_sample_block(
    excitation: object,
    response: object,
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
) -> SampleBlock:
    """Validate raw excitation/response sequences into a SampleBlock."""
    x = _as_1d_float_array(excitation, "excitation")
    y = _as_1d_float_array(response, "response")
    if x.shape[0] != y.shape[0]:
        raise InputValidationError(
            "excitation and response must have equal length",
            detail={
                "n_excitation": int(x.shape[0]),
                "n_response": int(y.shape[0]),
            },
        )
    if x.shape[0] > max_samples:
        raise ResourceExhaustedError(
            f"sample count {x.shape[0]} exceeds limit {max_samples}",
            detail={"n_samples": int(x.shape[0]), "max_samples": max_samples},
        )
    return SampleBlock(excitation=x, response=y)


def validate_params(params: EstimateParams, n_samples: int) -> EstimateParams:
    """Validate estimation parameters against the sample count."""
    if not isinstance(params.model_order, (int, np.integer)) or isinstance(
        params.model_order, bool
    ):
        raise InputValidationError(
            "model_order must be an integer",
            detail={"model_order": repr(params.model_order)},
        )
    if not 1 <= params.model_order <= MAX_MODEL_ORDER:
        raise InputValidationError(
            f"model_order must be in [1, {MAX_MODEL_ORDER}]",
            detail={"model_order": int(params.model_order)},
        )
    if n_samples < params.model_order + 1:
        raise InputValidationError(
            "need at least model_order + 1 samples to fit",
            detail={"n_samples": n_samples, "model_order": int(params.model_order)},
        )
    if not isinstance(params.delay, (int, np.integer)) or isinstance(params.delay, bool):
        raise InputValidationError(
            "delay must be an integer number of samples",
            detail={"delay": repr(params.delay)},
        )
    if abs(int(params.delay)) >= n_samples:
        raise InputValidationError(
            "delay magnitude must be smaller than the sample count",
            detail={"delay": int(params.delay), "n_samples": n_samples},
        )
    if not np.isfinite(params.regularization) or params.regularization < 0:
        raise InputValidationError(
            "regularization must be a finite value >= 0",
            detail={"regularization": params.regularization},
        )
    if not 0.0 <= params.holdout_fraction <= MAX_HOLDOUT_FRACTION:
        raise InputValidationError(
            f"holdout_fraction must be in [0, {MAX_HOLDOUT_FRACTION}]",
            detail={"holdout_fraction": params.holdout_fraction},
        )
    return params


def validate_request(
    excitation: object,
    response: object,
    params: EstimateParams,
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
) -> EstimateRequest:
    """Validate a full estimation request (samples + parameters)."""
    block = validate_sample_block(excitation, response, max_samples=max_samples)
    validate_params(params, block.n_samples)
    return EstimateRequest(samples=block, params=params, max_samples=max_samples)
