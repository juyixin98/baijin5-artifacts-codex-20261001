"""Core STFT / ISTFT signal algorithms.

Conventions (the "single source of truth" for the whole backend):

* **Unified window**: one window buffer is built per parameter set and used
  for analysis, synthesis *and* the OLA normalisation denominator (sum of
  w**2). The window is symmetric (``fftbins=False``) and centred inside the
  ``n_fft`` buffer when ``win_length < n_fft``.
* **Centre padding**: the input is zero-padded by ``n_fft // 2`` on both
  sides before framing, so analysis frame ``k`` is centred on input sample
  ``k * hop_length``.
* **Final crop**: after overlap-add and normalisation, the padding is removed
  and the result is cropped to exactly the requested output length.
* **Reconstructibility**: the OLA denominator over the kept region must be
  non-zero (above a relative tolerance); otherwise the parameter set is
  rejected with :class:`NotReconstructibleError`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import get_window

from .errors import (
    ConfigError,
    EmptySignalError,
    NotReconstructibleError,
    ShapeMismatchError,
)

#: Relative tolerance for the OLA normalisation denominator, against max(w**2).
DENOM_RTOL = 1e-8

#: Window names this backend accepts (mapped to scipy window names).
SUPPORTED_WINDOWS = {
    "hann": "hann",
    "hamming": "hamming",
    "blackman": "blackman",
    "rect": "boxcar",
}


@dataclass(frozen=True)
class StftParams:
    """A validated-once parameter set for one STFT/ISTFT computation."""

    n_fft: int
    win_length: int
    hop_length: int
    window: str = "hann"

    @property
    def n_bins(self) -> int:
        """Number of one-sided (rfft) frequency bins."""
        return self.n_fft // 2 + 1

    @property
    def pad(self) -> int:
        """Centre-padding applied on each side of the input."""
        return self.n_fft // 2


@dataclass(frozen=True)
class StftResult:
    spectrogram: np.ndarray  # shape (n_frames, n_bins), complex
    n_samples: int
    params: StftParams

    @property
    def n_frames(self) -> int:
        return int(self.spectrogram.shape[0])


@dataclass(frozen=True)
class IstftResult:
    samples: np.ndarray  # shape (length,), float
    min_denominator: float  # min OLA denominator over the kept region
    params: StftParams


def validate_params(params: StftParams) -> StftParams:
    """Reject parameter sets that are invalid or cannot reconstruct."""
    if params.n_fft < 1:
        raise ConfigError(f"n_fft must be >= 1, got {params.n_fft}")
    if params.win_length < 1:
        raise ConfigError(f"win_length must be >= 1, got {params.win_length}")
    if params.hop_length < 1:
        raise ConfigError(f"hop_length must be >= 1, got {params.hop_length}")
    if params.window not in SUPPORTED_WINDOWS:
        raise ConfigError(
            f"unsupported window {params.window!r}; "
            f"supported: {sorted(SUPPORTED_WINDOWS)}"
        )
    if params.win_length > params.n_fft:
        raise ConfigError(
            f"win_length ({params.win_length}) must be <= n_fft ({params.n_fft})"
        )
    if params.hop_length > params.win_length:
        # With hop > win_length there are gaps no frame covers, so the OLA
        # denominator is exactly zero there: reconstruction is impossible.
        raise NotReconstructibleError(
            f"hop_length ({params.hop_length}) > win_length ({params.win_length}) "
            "leaves uncovered gaps; the OLA denominator would be zero"
        )
    return params


def window_buffer(params: StftParams) -> np.ndarray:
    """The one unified window: symmetric, centred in an ``n_fft`` buffer."""
    validate_params(params)
    win = get_window(
        SUPPORTED_WINDOWS[params.window], params.win_length, fftbins=False
    )
    buf = np.zeros(params.n_fft, dtype=np.float64)
    start = (params.n_fft - params.win_length) // 2
    buf[start : start + params.win_length] = win
    return buf


def frame_center_sample(frame_index: int, hop_length: int) -> int:
    """Input-sample position on which frame ``frame_index`` is centred."""
    return frame_index * hop_length


def frame_sample_span(frame_index: int, params: StftParams) -> tuple[int, int]:
    """Half-open input-sample span ``[start, end)`` covered by a frame.

    May be negative / beyond the signal end near the boundaries because of
    centre padding; that is expected.
    """
    start = frame_index * params.hop_length - params.pad
    return start, start + params.n_fft


def _as_signal(x: np.ndarray | list[float]) -> np.ndarray:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 1:
        raise ShapeMismatchError(f"signal must be 1-D, got shape {arr.shape}")
    if arr.size == 0:
        raise EmptySignalError("signal has no samples")
    if not np.all(np.isfinite(arr)):
        raise ConfigError("signal contains NaN or infinite samples")
    return arr


def stft(x: np.ndarray | list[float], params: StftParams) -> StftResult:
    """Centre-padded, windowed, one-sided STFT.

    Returns a complex spectrogram of shape ``(n_frames, n_fft // 2 + 1)``.
    Frame ``k`` is centred on input sample ``k * hop_length``.
    """
    validate_params(params)
    sig = _as_signal(x)
    win = window_buffer(params)

    padded = np.concatenate(
        [np.zeros(params.pad), sig, np.zeros(params.pad)]
    )
    # Frames must cover the whole padded signal, not just the kept region:
    # a symmetric window's endpoints are zero, so the kept region needs a
    # `pad`-wide margin of frame coverage on each side for the OLA
    # denominator to stay non-zero everywhere we keep samples.
    if padded.size <= params.n_fft:
        n_frames = 1
    else:
        n_frames = 1 + -(-(padded.size - params.n_fft) // params.hop_length)
    extent = (n_frames - 1) * params.hop_length + params.n_fft
    if padded.size < extent:
        padded = np.concatenate([padded, np.zeros(extent - padded.size)])

    # Strided view: row k is padded[k*hop : k*hop + n_fft].
    frame_starts = np.arange(n_frames) * params.hop_length
    frames = (
        padded[frame_starts[:, None] + np.arange(params.n_fft)[None, :]] * win
    )
    spectrogram = np.fft.rfft(frames, n=params.n_fft, axis=1)
    return StftResult(spectrogram=spectrogram, n_samples=sig.size, params=params)


def ola_denominator(params: StftParams, n_frames: int) -> np.ndarray:
    """Overlap-added w**2 over the full reconstruction extent."""
    validate_params(params)
    if n_frames < 1:
        raise ConfigError(f"n_frames must be >= 1, got {n_frames}")
    win = window_buffer(params)
    wsq = win * win
    out_len = params.n_fft + params.hop_length * (n_frames - 1)
    den = np.zeros(out_len, dtype=np.float64)
    for k in range(n_frames):
        s = k * params.hop_length
        den[s : s + params.n_fft] += wsq
    return den


def check_ola_denominator(den: np.ndarray, params: StftParams) -> float:
    """Ensure the OLA denominator is non-zero over the kept region.

    Returns the minimum denominator value; raises NotReconstructibleError
    when any position falls at or below the relative tolerance.
    """
    win = window_buffer(params)
    wsq_max = float(np.max(win * win))
    if wsq_max <= 0.0:
        raise NotReconstructibleError("window is identically zero")
    tol = DENOM_RTOL * wsq_max
    den = np.asarray(den, dtype=np.float64)
    if den.size == 0:
        raise NotReconstructibleError("kept region is empty")
    min_den = float(np.min(den))
    if min_den <= tol:
        bad = int(np.argmin(den))
        raise NotReconstructibleError(
            f"OLA normalisation denominator is ~0 ({min_den:.3e} <= {tol:.3e}) "
            f"at kept-region offset {bad}; parameters cannot reconstruct"
        )
    return min_den


def istft(
    spectrogram: np.ndarray, params: StftParams, length: int
) -> IstftResult:
    """Inverse STFT: irfft, unified synthesis window, OLA, crop to ``length``.

    The normalisation denominator (overlap-added w**2) is checked to be
    non-zero over the kept region before dividing.
    """
    validate_params(params)
    S = np.asarray(spectrogram, dtype=np.complex128)
    if S.ndim == 1:
        S = S[None, :]
    if S.ndim != 2:
        raise ShapeMismatchError(
            f"spectrogram must be 2-D (n_frames, n_bins), got shape {S.shape}"
        )
    if S.shape[1] != params.n_bins:
        raise ShapeMismatchError(
            f"spectrogram has {S.shape[1]} frequency bins but n_fft="
            f"{params.n_fft} implies {params.n_bins}"
        )
    if S.shape[0] < 1:
        raise ShapeMismatchError("spectrogram has zero frames")
    if length < 1:
        raise ConfigError(f"length must be >= 1, got {length}")

    n_frames = S.shape[0]
    out_len = params.n_fft + params.hop_length * (n_frames - 1)
    if params.pad + length > out_len:
        raise ShapeMismatchError(
            f"requested length {length} exceeds reconstruction extent "
            f"{out_len - params.pad} for {n_frames} frames"
        )

    win = window_buffer(params)
    time_frames = np.fft.irfft(S, n=params.n_fft, axis=1) * win

    num = np.zeros(out_len, dtype=np.float64)
    den = np.zeros(out_len, dtype=np.float64)
    wsq = win * win
    for k in range(n_frames):
        s = k * params.hop_length
        num[s : s + params.n_fft] += time_frames[k]
        den[s : s + params.n_fft] += wsq

    keep = slice(params.pad, params.pad + length)
    min_den = check_ola_denominator(den[keep], params)
    return IstftResult(
        samples=num[keep] / den[keep], min_denominator=min_den, params=params
    )


def onesided_to_full(
    spectrogram: np.ndarray, n_fft: int
) -> np.ndarray:
    """Expand a one-sided (rfft) spectrum to the full ``n_fft`` spectrum.

    Conjugate symmetry is restored *without* duplicating the DC bin, and
    without duplicating the Nyquist bin when ``n_fft`` is even (odd ``n_fft``
    has no Nyquist bin).
    """
    S = np.asarray(spectrogram, dtype=np.complex128)
    if S.ndim == 1:
        S = S[None, :]
    expected = n_fft // 2 + 1
    if S.shape[-1] != expected:
        raise ShapeMismatchError(
            f"one-sided spectrum has {S.shape[-1]} bins but n_fft={n_fft} "
            f"implies {expected}"
        )
    if n_fft % 2 == 0:
        # Bins 1 .. n_fft/2 - 1 mirrored; DC (0) and Nyquist (n_fft/2) kept once.
        tail = np.conj(S[..., 1:-1])[..., ::-1]
    else:
        # No Nyquist bin; mirror bins 1 .. end.
        tail = np.conj(S[..., 1:])[..., ::-1]
    return np.concatenate([S, tail], axis=-1)
