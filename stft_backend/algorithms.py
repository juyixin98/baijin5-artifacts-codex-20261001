"""Batch STFT / ISTFT core implemented directly with NumPy.

Conventions (identical on the forward and inverse path):

* **Boundary**: ``nperseg // 2`` zeros are prepended and appended ("center"
  extension), so frame 0 is centered on sample 0 of the original signal.
* **Trailing padding**: zero samples are added so the extended signal fills
  an integer number of frames spaced by ``hop``.
* **Window**: the same array is used for analysis and synthesis.
* **Normalization**: overlap-add divides by the accumulated window *energy*
  ``sum_m |w[n - m*hop]|^2`` (least-squares / weighted overlap-add).
* **Trimming**: the boundary extension is removed and the result is cut to
  the recorded original length — padding never leaks into the output.

These four steps are the single source of truth shared by :mod:`streaming`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .errors import ErrorCode, StftError
from .numeric import (
    NOLA_ABS_TOL,
    NOLA_REL_TOL,
    ensure_real_finite,
    nola_diagnostics,
)

# Imaginary parts at DC / Nyquist that can only come from numerical noise.
HERMITIAN_TOL = 1e-8
# Output bins whose normalized weight is below this are declared uncovered.
COVERAGE_REL_TOL = 1e-10


@dataclass(frozen=True)
class TransformMeta:
    """Everything needed to invert a spectrum and locate its frames."""

    nperseg: int
    hop: int
    nfft: int
    window: np.ndarray
    input_length: int
    onesided: bool
    n_frames: int
    padded_length: int

    def boundary_pad(self) -> int:
        return self.nperseg // 2

    def frame_start(self, frame_index: int) -> int:
        """Original-signal index of the first sample covered by a frame.

        May be negative: early frames reach into the boundary extension.
        """

        return frame_index * self.hop - self.boundary_pad()

    def frame_center(self, frame_index: int) -> int:
        """Original-signal index aligned with the window peak.

        With ``nperseg // 2`` samples of boundary extension the DFT-even
        window peak of frame ``m`` always lands on original sample
        ``m * hop`` — for even and odd ``nperseg`` alike. This matches the
        scipy ``t`` time vector under ``boundary='zeros'``.
        """

        return frame_index * self.hop

    def frame_positions(self) -> list[dict]:
        return [
            {
                "frame_index": m,
                "start_sample": self.frame_start(m),
                "center_sample": self.frame_center(m),
            }
            for m in range(self.n_frames)
        ]


def _trailing_pad(extended_length: int, nperseg: int, hop: int) -> int:
    return (-((extended_length - nperseg) % hop)) % hop


def _framed_signal(
    x: np.ndarray, nperseg: int, hop: int
) -> tuple[np.ndarray, int, int]:
    """Apply boundary + trailing extension and return ``(extended, pad, trail)``."""

    pad = nperseg // 2
    extended = np.pad(x, pad, mode="constant", constant_values=0.0)
    trail = _trailing_pad(len(extended), nperseg, hop)
    if trail:
        extended = np.pad(extended, (0, trail), mode="constant", constant_values=0.0)
    return extended, pad, trail


def stft(
    x: np.ndarray,
    *,
    nperseg: int,
    hop: int,
    nfft: int,
    window: np.ndarray,
    onesided: bool = True,
) -> tuple[np.ndarray, TransformMeta]:
    """Short-time Fourier transform of a real 1-D signal.

    Returns the spectrum with shape ``(freq_bins, n_frames)`` and metadata.
    Parameters must already have passed
    :func:`stft_backend.numeric.validate_transform_params`.
    """

    signal = ensure_real_finite(x, stage="stft")
    if signal.size == 0:
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            "Signal must contain at least one sample.",
            stage="stft",
        )
    nola_diagnostics(window, hop, nfft)

    extended, _pad, trail = _framed_signal(signal, nperseg, hop)
    n_frames = (len(extended) - nperseg) // hop + 1

    if onesided:
        spectrum = np.empty((nfft // 2 + 1, n_frames), dtype=np.complex128)
        transform = np.fft.rfft
    else:
        spectrum = np.empty((nfft, n_frames), dtype=np.complex128)
        transform = np.fft.fft

    for m in range(n_frames):
        segment = extended[m * hop : m * hop + nperseg] * window
        if nfft > nperseg:
            segment = np.pad(segment, (0, nfft - nperseg))
        spectrum[:, m] = transform(segment)

    meta = TransformMeta(
        nperseg=nperseg,
        hop=hop,
        nfft=nfft,
        window=window,
        input_length=int(signal.size),
        onesided=onesided,
        n_frames=n_frames,
        padded_length=len(extended),
    )
    return spectrum, meta


def _expected_freq_bins(nfft: int, onesided: bool) -> int:
    return nfft // 2 + 1 if onesided else nfft


def validate_spectrum_shape(
    spectrum: np.ndarray, nfft: int, onesided: bool
) -> int:
    if spectrum.ndim != 2:
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            f"Spectrum must be 2-D (freq_bins, frames), got shape {spectrum.shape}.",
            stage="spectrum_validation",
            details={"shape": list(spectrum.shape)},
        )
    expected = _expected_freq_bins(nfft, onesided)
    if spectrum.shape[0] != expected:
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            f"Spectrum has {spectrum.shape[0]} frequency bins; expected {expected} "
            f"for nfft={nfft} ({'one' if onesided else 'two'}-sided).",
            stage="spectrum_validation",
            details={
                "shape": list(spectrum.shape),
                "expected_freq_bins": expected,
                "nfft": nfft,
                "onesided": onesided,
            },
        )
    if spectrum.shape[1] == 0:
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            "Spectrum contains zero frames.",
            stage="spectrum_validation",
        )
    if not np.all(np.isfinite(spectrum.real)) or not np.all(np.isfinite(spectrum.imag)):
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            "Spectrum contains non-finite values (NaN or infinity).",
            stage="spectrum_validation",
        )
    return spectrum.shape[1]


def _recover_full_spectrum(
    spectrum: np.ndarray, nfft: int, hermitian_tol: float
) -> np.ndarray:
    """Rebuild a two-sided spectrum via conjugate symmetry.

    DC is kept once as-is and Nyquist (only present when ``nfft`` is even)
    is likewise kept once — neither is duplicated. Negative-frequency bins
    are the conjugates of their positive-frequency mirrors.
    """

    dc_max_imag = float(np.max(np.abs(spectrum[0, :].imag)))
    if dc_max_imag > hermitian_tol:
        raise StftError(
            ErrorCode.ASYMMETRIC_SPECTRUM,
            "DC bin must be real for a real signal; its imaginary part "
            f"reaches {dc_max_imag:.3e} (tol {hermitian_tol:.0e}).",
            stage="hermitian_check",
            details={"bin": "dc", "max_imag": dc_max_imag, "tolerance": hermitian_tol},
        )
    if nfft % 2 == 0:
        nyq_max_imag = float(np.max(np.abs(spectrum[-1, :].imag)))
        if nyq_max_imag > hermitian_tol:
            raise StftError(
                ErrorCode.ASYMMETRIC_SPECTRUM,
                "Nyquist bin must be real for a real signal; its imaginary "
                f"part reaches {nyq_max_imag:.3e} (tol {hermitian_tol:.0e}).",
                stage="hermitian_check",
                details={
                    "bin": "nyquist",
                    "max_imag": nyq_max_imag,
                    "tolerance": hermitian_tol,
                },
            )
        mirrored = spectrum[-2:0:-1, :]
    else:
        # Odd nfft: no Nyquist bin; full[nfft-k] = conj(full[k]) for
        # k = 1 .. nfft//2, so the last one-sided row (k = nfft//2) is
        # itself mirrored.
        mirrored = spectrum[:0:-1, :]
    return np.concatenate([spectrum, np.conj(mirrored)], axis=0)


def _normalize_and_trim(
    numerator: np.ndarray,
    denominator: np.ndarray,
    *,
    n_frames: int,
    nperseg: int,
    hop: int,
    signal_length: int | None,
) -> np.ndarray:
    """Divide by the OLA denominator, remove extension, trim to length."""

    pad = nperseg // 2
    # Length the encoder side would have produced for the real signal.
    inferred = (n_frames - 1) * hop + nperseg - 2 * pad
    if inferred <= 0:
        raise StftError(
            ErrorCode.SPECTRUM_SHAPE_MISMATCH,
            "Not enough frames to cover any original-signal samples.",
            stage="istft",
            details={"n_frames": n_frames, "inferred_length": inferred},
        )
    target_length = signal_length if signal_length is not None else inferred
    if target_length <= 0:
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            f"signal_length must be positive, got {target_length}.",
            stage="istft",
            details={"signal_length": target_length},
        )

    coverage_tol = max(NOLA_ABS_TOL, NOLA_REL_TOL * float(denominator.max(initial=0.0)))
    # Original samples live in extended coordinates [pad, pad + target).
    # Extend the coverage arrays with explicit zeros when the caller asks
    # for more samples than the received frames can possibly cover —
    # NumPy's out-of-bounds slice would silently drop those positions.
    needed = pad + target_length
    if needed > denominator.size:
        denominator = np.pad(denominator, (0, needed - denominator.size))
        numerator = np.pad(numerator, (0, needed - numerator.size))
    requested = denominator[pad : pad + target_length]
    weak = np.flatnonzero(requested <= coverage_tol)
    if weak.size:
        first_original = int(weak[0])
        raise StftError(
            ErrorCode.UNCOVERED_SAMPLES,
            "Overlap-add normalization denominator is zero (or below "
            f"tolerance {coverage_tol:.2e}) for {weak.size} requested "
            f"sample(s); first uncovered sample at index {first_original}. "
            "The provided frames do not cover the requested signal length.",
            stage="ola_normalize",
            details={
                "first_uncovered_sample": first_original,
                "uncovered_count": int(weak.size),
                "requested_length": target_length,
                "covered_inferred_length": inferred,
                "tolerance": coverage_tol,
            },
        )

    safe_denom = np.where(denominator > coverage_tol, denominator, 1.0)
    reconstructed = numerator / safe_denom
    return reconstructed[pad : pad + target_length]


def istft(
    spectrum: np.ndarray,
    *,
    nperseg: int,
    hop: int,
    nfft: int,
    window: np.ndarray,
    onesided: bool = True,
    signal_length: int | None = None,
    hermitian_tol: float = HERMITIAN_TOL,
) -> np.ndarray:
    """Inverse STFT via weighted overlap-add.

    Parameters must already have passed
    :func:`stft_backend.numeric.validate_transform_params`.
    """

    nola_diagnostics(window, hop, nfft)
    spec = np.asarray(spectrum, dtype=np.complex128)
    n_frames = validate_spectrum_shape(spec, nfft, onesided)
    if signal_length is not None and (
        not isinstance(signal_length, int)
        or isinstance(signal_length, bool)
        or signal_length <= 0
    ):
        raise StftError(
            ErrorCode.INVALID_PARAMETER,
            "signal_length must be a positive integer when provided.",
            stage="istft",
        )

    if onesided:
        full = _recover_full_spectrum(spec, nfft, hermitian_tol)
    else:
        full = spec

    total = (n_frames - 1) * hop + nperseg
    numerator = np.zeros(total, dtype=np.float64)
    denominator = np.zeros(total, dtype=np.float64)

    for m in range(n_frames):
        frame = np.fft.ifft(full[:, m], n=nfft)[:nperseg]
        start = m * hop
        end = start + nperseg
        numerator[start:end] += np.real(frame * window)
        denominator[start:end] += np.abs(window) ** 2

    return _normalize_and_trim(
        numerator,
        denominator,
        n_frames=n_frames,
        nperseg=nperseg,
        hop=hop,
        signal_length=signal_length,
    )


def roundtrip_error(x: np.ndarray, y: np.ndarray) -> dict:
    """Concrete error summary used by the API and tests."""

    diff = np.abs(x - y)
    power = float(np.dot(x, x))
    rel_rms = (
        float(math.sqrt(np.mean(diff**2) / (np.mean(x**2))))
        if np.mean(x**2) > 0
        else float("nan")
    )
    return {
        "max_abs_error": float(diff.max(initial=0.0)),
        "rmse": float(np.sqrt(np.mean(diff**2))),
        "relative_rms_error": rel_rms,
        "signal_energy": power,
        "length_preserved": bool(x.shape == y.shape),
    }
