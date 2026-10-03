"""Boundary convolution (Toeplitz) matrices.

The forward model is causal FIR convolution:

    y[t] = sum_{k=0}^{L-1} h[k] * x[t-k],   t = 0 .. N-1

`convolution_matrix` builds the matrix X such that X @ h reproduces the
first N samples of the full convolution. Two boundary modes:

- "zero_pad" (default): shape (N, L). Rows near t=0 treat x[t-k] as 0
  for t-k < 0. The matrix has exactly one row per observed output
  sample, so it is consistent with the observation length.
- "valid": shape (N - L + 1, L). Only rows whose taps all fall inside
  the observed excitation; row 0 corresponds to output index L-1.
"""

from __future__ import annotations

import numpy as np

from .errors import InputValidationError

_MODES = ("zero_pad", "valid")


def convolution_matrix(excitation: np.ndarray, order: int, mode: str = "zero_pad") -> np.ndarray:
    """Build the causal convolution matrix for `excitation` and `order` taps."""
    x = np.asarray(excitation, dtype=float)
    if x.ndim != 1:
        raise InputValidationError("excitation must be 1-D")
    if mode not in _MODES:
        raise InputValidationError(
            f"unknown convolution mode {mode!r}", detail={"modes": list(_MODES)}
        )
    n = x.shape[0]
    if order < 1:
        raise InputValidationError("order must be >= 1", detail={"order": order})
    if mode == "valid" and order > n:
        raise InputValidationError(
            "valid mode requires order <= n_samples",
            detail={"order": order, "n_samples": n},
        )
    # Column k of the zero-padded matrix is x shifted down by k.
    # First column is x itself; first row is [x[0], 0, 0, ...].
    first_col = x
    first_row = np.zeros(order)
    first_row[0] = x[0]
    matrix = _toeplitz(first_col, first_row)
    if mode == "valid":
        matrix = matrix[order - 1 :]
    return matrix


def _toeplitz(first_col: np.ndarray, first_row: np.ndarray) -> np.ndarray:
    """Toeplitz matrix from first column/row (avoids a scipy dependency here)."""
    n_rows = first_col.shape[0]
    n_cols = first_row.shape[0]
    # Row t, column k = first_col[t - k] when t >= k else first_row[k - t].
    row_idx, col_idx = np.ogrid[0:n_rows, 0:n_cols]
    diff = row_idx - col_idx
    matrix = np.where(diff >= 0, first_col[np.clip(diff, 0, None)], first_row[np.clip(-diff, 0, None)])
    return matrix


def predict(excitation: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    """Predict the response of `coefficients` to `excitation`.

    Returns exactly len(excitation) samples (zero-padded boundary), i.e.
    the same convention as convolution_matrix(mode="zero_pad").
    """
    h = np.asarray(coefficients, dtype=float)
    x = np.asarray(excitation, dtype=float)
    full = np.convolve(x, h, mode="full")
    return full[: x.shape[0]]
