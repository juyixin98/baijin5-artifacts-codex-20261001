"""Evaluation metrics computed against the known clean signal.

The success criterion is *closeness to ground truth*, never "the output
got quieter" — an adaptive filter can trivially reduce output energy by
erasing the desired signal too.
"""

from __future__ import annotations

import math

import numpy as np

from app.errors import InputValidationError


def _check_pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        raise InputValidationError(
            "metric inputs must have identical shape",
            detail={"a": a.shape, "b": b.shape},
        )
    if a.size == 0:
        raise InputValidationError("metric inputs must be non-empty")
    return a, b


def mse(a: np.ndarray, b: np.ndarray) -> float:
    a, b = _check_pair(a, b)
    return float(np.mean((a - b) ** 2))


def snr_improvement_db(
    primary: np.ndarray, residual: np.ndarray, clean: np.ndarray
) -> float:
    """SNR gain of the canceller output vs. the raw primary, in dB.

    ``10 * log10( ||primary - clean||^2 / ||residual - clean||^2 )``.
    Positive means the residual is closer to the clean signal than the
    primary was. Returns ``-inf`` if the residual is *further* from clean
    with zero numerator margin, and ``+inf`` only for a perfect residual.
    """
    primary, clean = _check_pair(primary, clean)
    residual, clean = _check_pair(residual, clean)
    noise_before = float(np.sum((primary - clean) ** 2))
    noise_after = float(np.sum((residual - clean) ** 2))
    if noise_before == 0.0:
        raise InputValidationError("primary already equals clean; SNR gain undefined")
    if noise_after == 0.0:
        return math.inf
    return 10.0 * math.log10(noise_before / noise_after)


def coefficient_error_norm(weights: np.ndarray, true_coeffs: np.ndarray) -> float:
    """||w - w_true||_2 with explicit length checking."""
    weights = np.asarray(weights, dtype=np.float64)
    true_coeffs = np.asarray(true_coeffs, dtype=np.float64)
    if weights.shape != true_coeffs.shape:
        raise InputValidationError(
            "coefficient length mismatch",
            detail={"weights": weights.shape, "true": true_coeffs.shape},
        )
    return float(np.linalg.norm(weights - true_coeffs))
