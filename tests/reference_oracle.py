"""Independent reference oracle for the STFT/ISTFT test suite.

This module deliberately does **not** import anything from
``stft_backend.algorithms`` / ``streaming`` / ``numeric``. Expected values
are generated three independent ways:

1. **DFT-matrix STFT** — every frame transform is an explicit matrix
   multiply, no FFT library calls;
2. **closed-form OLA constants** — a tiny window/hop case whose exact
   reconstruction is written out by hand;
3. **SciPy cross-check** — ``scipy.signal.stft/istft`` as a second
   implementation, used only to corroborate conventions (frame time
   positions, round-trip accuracy), never as an assertion's sole
   authority.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import get_window


def make_window(name: str, nperseg: int) -> np.ndarray:
    return np.asarray(get_window(name, nperseg, fftbins=True), dtype=np.float64)


def boundary_extension(x: np.ndarray, nperseg: int, hop: int) -> np.ndarray:
    """Center with nperseg//2 zeros each side, then align to the hop grid."""

    pad = nperseg // 2
    extended = np.concatenate(
        [np.zeros(pad), np.asarray(x, dtype=np.float64), np.zeros(pad)]
    )
    trail = (-((len(extended) - nperseg) % hop)) % hop
    if trail:
        extended = np.concatenate([extended, np.zeros(trail)])
    return extended


def dft_matrix(nfft: int) -> np.ndarray:
    k = np.arange(nfft)
    return np.exp(-2j * np.pi * np.outer(k, k) / nfft)


def oracle_stft(
    x: np.ndarray,
    *,
    nperseg: int,
    hop: int,
    nfft: int | None = None,
    window: np.ndarray | str = "hann",
    onesided: bool = True,
) -> np.ndarray:
    """STFT via explicit DFT matrix multiplication.

    Returns shape ``(freq_bins, n_frames)`` like the backend.
    """

    nfft = nfft or nperseg
    if isinstance(window, str):
        window = make_window(window, nperseg)
    extended = boundary_extension(x, nperseg, hop)
    n_frames = (len(extended) - nperseg) // hop + 1
    F = dft_matrix(nfft)
    rows = nfft // 2 + 1 if onesided else nfft
    spectrum = np.empty((rows, n_frames), dtype=np.complex128)
    for m in range(n_frames):
        segment = extended[m * hop : m * hop + nperseg] * window
        padded = np.concatenate([segment, np.zeros(nfft - nperseg)])
        spectrum[:, m] = F[:rows] @ padded
    return spectrum


def oracle_istft_nfft_grid(
    spectrum_full: np.ndarray,
    *,
    nperseg: int,
    hop: int,
    nfft: int,
    window: np.ndarray,
    length: int,
) -> np.ndarray:
    """Weighted OLA inversion with the denominator summed by definition.

    The numerator and denominator are accumulated independently from the
    actual frame positions (no periodic tiling), so the extension
    boundary samples — where the periodic interior formula would invent
    contributions from non-existent negative-index frames — are correct.
    """

    n_frames = spectrum_full.shape[1]
    Fi = np.conj(dft_matrix(nfft)) / nfft
    total = (n_frames - 1) * hop + nperseg
    numerator = np.zeros(total)
    denominator = np.zeros(total)
    for m in range(n_frames):
        frame = (Fi @ spectrum_full[:, m])[:nperseg].real
        numerator[m * hop : m * hop + nperseg] += frame * window
        denominator[m * hop : m * hop + nperseg] += window**2
    pad = nperseg // 2
    safe = np.where(denominator > 0, denominator, 1.0)
    extended_hat = numerator / safe
    return np.real(extended_hat[pad : pad + length])


# --------------------------------------------------------------------------
# Closed-form expectation.  nperseg=4, hop=2, DFT-even Hann window is
# w = [0, 1, 1, 0]; w^2 = [0, 1, 1, 0]. With hop=2 the two non-zero
# entries of consecutive frames tile the extended grid without overlap:
# frame m covers extended positions 2m+1 and 2m+2, so the OLA denominator
# equals exactly 1 at every covered interior position and weighted OLA
# reduces to exact reconstruction. The trimmed result must equal x to
# machine precision for every input length.
# --------------------------------------------------------------------------

HANN4 = np.array([0.0, 1.0, 1.0, 0.0])


def expected_hann4_hop2_reconstruction(x: np.ndarray) -> np.ndarray:
    """Exact reconstruction for w=[0,1,1,0], hop=2, computed by hand.

    Every windowed segment is transformed/inverted with explicit DFT
    matrices (no FFT calls), accumulated with plain addition because the
    interior denominator is exactly 1, then trimmed.
    """

    x = np.asarray(x, dtype=np.float64)
    extended = boundary_extension(x, 4, 2)
    out = np.zeros_like(extended)
    m = 0
    while m * 2 + 4 <= len(extended):
        seg = extended[m * 2 : m * 2 + 4]
        windowed = seg * HANN4
        spec = dft_matrix(4) @ windowed
        recovered = (np.conj(dft_matrix(4)) / 4.0) @ spec
        out[m * 2 : m * 2 + 4] += recovered.real * HANN4
        m += 1
    return out[2 : 2 + x.size]


def expected_frame_times_scipy(n: int, nperseg: int, hop: int) -> np.ndarray:
    """Frame centers under boundary='zeros': exactly m*hop (scipy t)."""

    import scipy.signal as ss

    x = np.zeros(n)
    _f, t, _Z = ss.stft(
        x,
        fs=1.0,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg - hop,
        boundary="zeros",
        padded=True,
    )
    return t
