"""Evaluation metrics against a known clean reference.

Success is judged ONLY against the known clean signal (and, when the true
plant is available, against the true coefficient vector). A drop in output
energy is explicitly NOT a success criterion: a filter that outputs zeros
has minimal output energy and maximal distortion.
"""

from __future__ import annotations

import numpy as np

from app.errors import InputValidationError


def _as_checked_pair(a: np.ndarray, b: np.ndarray, name_a: str, name_b: str) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 1 or a.size == 0:
        raise InputValidationError(
            f"{name_a} and {name_b} must be non-empty 1-D arrays of equal length",
            reason="length_mismatch",
            detail={name_a: a.shape[0] if a.ndim else None,
                    name_b: b.shape[0] if b.ndim else None},
        )
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise InputValidationError(
            "metric inputs must be finite",
            reason="non_finite_input",
        )
    return a, b


def residual_mse(residual: np.ndarray, clean: np.ndarray) -> float:
    """Mean squared error between the filter residual and the clean signal."""
    residual, clean = _as_checked_pair(residual, clean, "residual", "clean")
    return float(np.mean((residual - clean) ** 2))


def noise_reduction_db(desired: np.ndarray, residual: np.ndarray, clean: np.ndarray) -> float:
    """Noise power before vs. after filtering, both measured against clean.

    ``10 * log10( mean((desired - clean)^2) / mean((residual - clean)^2) )``.
    Positive values mean the residual is closer to clean than the input was.
    """
    desired, clean = _as_checked_pair(desired, clean, "desired", "clean")
    residual, _ = _as_checked_pair(residual, clean, "residual", "clean")
    before = float(np.mean((desired - clean) ** 2))
    after = float(np.mean((residual - clean) ** 2))
    if before <= 0.0:
        raise InputValidationError(
            "desired signal carries no noise against clean; "
            "noise_reduction_db is undefined",
            reason="no_noise_present",
        )
    if after <= 0.0:
        return float("inf")
    return float(10.0 * np.log10(before / after))


def coefficient_error_db(estimated: np.ndarray, truth: np.ndarray) -> float:
    """Normalised coefficient misalignment: 10*log10(||w-w*||^2 / ||w*||^2)."""
    estimated, truth = _as_checked_pair(estimated, truth, "estimated", "truth")
    denom = float(truth @ truth)
    if denom <= 0.0:
        raise InputValidationError(
            "true coefficient vector is zero; misalignment is undefined",
            reason="zero_truth",
        )
    num = float((estimated - truth) @ (estimated - truth))
    if num <= 0.0:
        return float("-inf")
    return float(10.0 * np.log10(num / denom))
