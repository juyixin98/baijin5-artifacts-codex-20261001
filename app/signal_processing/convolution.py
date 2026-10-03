"""Boundary-consistent convolution (Toeplitz) design matrices.

For the FIR model

    y[n] = sum_{k=0}^{L-1} h[k] * x[n-k] + e[n]

the design matrix X has rows of reversed length-L windows of the excitation,
so that ``X @ h`` reproduces the modeled output segment. The matrix and the
observation vector are always built together by
:func:`build_design_and_observation`, which guarantees that the number of
matrix rows matches the observation length exactly — this is the "boundary
consistency" contract of the service.

Two explicit boundary modes are supported:

- ``valid``    — only rows where the full length-L window lies inside the
  observed excitation. X has shape ``(N - L + 1, L)`` and row ``i`` predicts
  ``y[L - 1 + i]``. No assumptions about the signal before sample 0.
- ``zero_pad`` — the excitation is assumed zero for ``n < 0``. X has shape
  ``(N, L)`` and row ``i`` predicts ``y[i]``.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from ..errors import InputError

BoundaryMode = Literal["valid", "zero_pad"]

BOUNDARY_MODES: tuple[str, ...] = ("valid", "zero_pad")


def build_convolution_matrix(
    excitation: np.ndarray, order: int, boundary: BoundaryMode = "valid"
) -> np.ndarray:
    """Build the Toeplitz design matrix for FIR estimation.

    Args:
        excitation: 1-D excitation samples, length N.
        order: FIR model order L (number of taps), 1 <= L <= N.
        boundary: ``valid`` or ``zero_pad`` (see module docstring).

    Returns:
        Matrix of shape ``(N - L + 1, L)`` (valid) or ``(N, L)`` (zero_pad).
    """
    x = np.asarray(excitation, dtype=float)
    if x.ndim != 1:
        raise InputError(
            "excitation must be a 1-D sequence",
            reason="excitation_not_1d",
            detail={"ndim": int(x.ndim)},
        )
    n = x.size
    if not 1 <= order <= n:
        raise InputError(
            "model order must satisfy 1 <= order <= len(excitation)",
            reason="order_out_of_range",
            detail={"order": order, "n_samples": n},
        )
    if boundary == "valid":
        padded = x
    elif boundary == "zero_pad":
        padded = np.concatenate([np.zeros(order - 1), x])
    else:
        raise InputError(
            f"unknown boundary mode {boundary!r}",
            reason="unknown_boundary_mode",
            detail={"boundary": boundary, "allowed": list(BOUNDARY_MODES)},
        )
    # sliding_window_view row i = padded[i : i + L]; reversing each row gives
    # [x[i+L-1], x[i+L-2], ..., x[i]] so that row @ h = sum_k h[k] x[i+L-1-k].
    windows = np.lib.stride_tricks.sliding_window_view(padded, order)
    return np.ascontiguousarray(windows[:, ::-1])


def build_design_and_observation(
    excitation: np.ndarray,
    response: np.ndarray,
    order: int,
    boundary: BoundaryMode = "valid",
) -> tuple[np.ndarray, np.ndarray]:
    """Build the design matrix and the matching observation vector.

    The returned pair ``(X, y_obs)`` always satisfies
    ``X.shape[0] == y_obs.shape[0]``; the boundary mode decides which
    response samples are observed.
    """
    y = np.asarray(response, dtype=float)
    if y.ndim != 1:
        raise InputError(
            "response must be a 1-D sequence",
            reason="response_not_1d",
            detail={"ndim": int(y.ndim)},
        )
    matrix = build_convolution_matrix(excitation, order, boundary)
    if y.size != np.asarray(excitation).size:
        raise InputError(
            "excitation and response must have equal length after alignment",
            reason="length_mismatch",
            detail={"n_excitation": int(np.asarray(excitation).size), "n_response": int(y.size)},
        )
    if boundary == "valid":
        observation = y[order - 1 :]
    else:
        observation = y
    assert matrix.shape[0] == observation.shape[0], "boundary contract violated"
    return matrix, observation
