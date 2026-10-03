"""Numerical preconditions: parameter validation and reconstruction checks.

The key reconstruction precondition for overlap-add inversion is NOLA
(nonzero overlap-add): with the same analysis/synthesis window the
accumulated window energy

    D(q) = sum_m |w[q - m*hop]|^2

must be strictly positive at every reconstructed sample. NOLA is
necessary and sufficient for least-squares reconstruction; COLA
(``sum_m w[n - m*hop] == 1``) is the stricter, sufficient-only special
case and is never required.

The denominator is ``hop``-periodic in the interior, so one representative
period next to *each* extension boundary is enough: the first ``hop``
original-sample positions (where no frames with negative index exist) and
the last ``hop`` positions before the right extension (where no further
frames exist). Checking the nfft grid instead would be valid only when
``hop`` divides ``nfft``.
"""

from __future__ import annotations

import numpy as np

from .errors import ErrorCode, StftError
from .windows import WindowSpec, resolve_window

# Relative floor used when deciding whether an OLA bin counts as zero.
NOLA_REL_TOL = 1e-10
# Absolute floor so an all-zero window is rejected even without a scale.
NOLA_ABS_TOL = 1e-12
# Maximum number of zero-bin indices reported back in error details.
_MAX_REPORTED_ZEROS = 16


def validate_scalar_parameters(nperseg: int, hop: int, nfft: int) -> None:
    """Check the integer parameter triple (window length / hop / FFT length).

    Both even and odd ``nperseg`` / ``nfft`` are accepted.
    """

    if not isinstance(nperseg, int) or isinstance(nperseg, bool):
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            "nperseg must be an integer.",
            stage="parameters",
        )
    if not isinstance(hop, int) or isinstance(hop, bool):
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            "hop must be an integer.",
            stage="parameters",
        )
    if not isinstance(nfft, int) or isinstance(nfft, bool):
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            "nfft must be an integer.",
            stage="parameters",
        )
    if nperseg < 2:
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            f"nperseg must be >= 2, got {nperseg}.",
            stage="parameters",
            details={"nperseg": nperseg},
        )
    if hop < 1 or hop >= nperseg:
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            f"hop must satisfy 1 <= hop < nperseg, got hop={hop}, "
            f"nperseg={nperseg}. hop >= nperseg leaves gaps between frames.",
            stage="parameters",
            details={"hop": hop, "nperseg": nperseg},
        )
    if nfft < nperseg:
        raise StftError(
            ErrorCode.NFFT_TOO_SMALL,
            f"nfft must be >= nperseg, got nfft={nfft}, nperseg={nperseg}.",
            stage="parameters",
            details={"nfft": nfft, "nperseg": nperseg},
        )


def nola_period_denominator(window: np.ndarray, hop: int) -> np.ndarray:
    """Fold ``|w|^2`` circularly by ``hop`` into one denominator period.

    Returns an array of length ``hop`` where bin ``r`` holds
    ``sum_{k ≡ r (mod hop)} |w[k]|^2``. The OLA denominator repeats this
    period for every interior sample. This is the same construction
    ``scipy.signal.check_NOLA`` uses (the trailing remainder wraps into
    the first bins via ``win[-(rem):]``).
    """

    nperseg = window.shape[0]
    period = np.zeros(hop, dtype=np.float64)
    for start in range(0, nperseg, hop):
        seg = window[start : start + hop]
        period[: seg.size] += np.abs(seg) ** 2
    return period


def nola_diagnostics(window: np.ndarray, hop: int, nfft: int | None = None) -> dict:
    """Return NOLA diagnostics, raising :class:`StftError` on violation.

    Only the folded period matters: the OLA denominator is ``hop``
    periodic, and the period at the trailing frame boundary is merely a
    permutation of this one (mapping residue ``r`` to
    ``(nperseg - 1 - r) mod hop``), so a single minimum suffices. The
    definitive per-sample check against the *real* denominator still
    happens at reconstruction time. ``nfft`` is irrelevant to the
    condition and accepted only for call-site symmetry.
    """

    period = nola_period_denominator(window, hop)
    peak = float(period.max(initial=0.0))
    tolerance = max(NOLA_ABS_TOL, NOLA_REL_TOL * peak)
    zero_bins = np.flatnonzero(period <= tolerance)
    if zero_bins.size:
        raise StftError(
            ErrorCode.NOLA_VIOLATION,
            "Window/hop pair fails the NOLA condition: the overlap-add "
            "normalization denominator is zero at one or more sample "
            "residues, so reconstruction is impossible.",
            stage="nola_check",
            details={
                "hop": hop,
                "min_denominator": float(period.min()),
                "tolerance": tolerance,
                "zero_bin_count": int(zero_bins.size),
                "zero_bins_first": [int(i) for i in zero_bins[:_MAX_REPORTED_ZEROS]],
            },
        )
    return {
        "min_denominator": float(period.min()),
        "max_denominator": peak,
        "tolerance": tolerance,
        "condition": "NOLA",
    }


def validate_transform_params(
    nperseg: int,
    hop: int,
    nfft: int | None,
    window_spec: WindowSpec,
) -> tuple[int, int, int, np.ndarray]:
    """Validate and resolve the full parameter set; return concrete values."""

    validate_scalar_parameters(nperseg, hop, nfft if nfft is not None else nperseg)
    if nfft is None:
        nfft = nperseg
    window = resolve_window(window_spec, nperseg)
    nola_diagnostics(window, hop, nfft)
    return nperseg, hop, nfft, window


def ensure_real_finite(samples: np.ndarray, *, stage: str = "validation") -> np.ndarray:
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim != 1:
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            f"Signal must be one-dimensional, got shape {arr.shape}.",
            stage=stage,
            details={"shape": list(arr.shape)},
        )
    if not np.all(np.isfinite(arr)):
        raise StftError(
            ErrorCode.NON_FINITE_SIGNAL,
            "Signal contains non-finite samples (NaN or infinity).",
            stage=stage,
        )
    return arr
