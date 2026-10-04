"""Numerical tests for the STFT/ISTFT core.

Reference answers here are produced by a hand-rolled naive DFT and by
hand-computed expected values — not by the core under test — so the tests
fail if the core's FFT usage, framing, padding or cropping drifts.
"""

import cmath

import numpy as np
import pytest

from app.errors import (
    ConfigError,
    EmptySignalError,
    NotReconstructibleError,
    ShapeMismatchError,
)
from app.stft_core import (
    StftParams,
    check_ola_denominator,
    frame_center_sample,
    frame_sample_span,
    istft,
    ola_denominator,
    onesided_to_full,
    stft,
    window_buffer,
)


def naive_dft(x):
    """O(n^2) DFT from the definition — independent of numpy.fft."""
    n = len(x)
    return [
        sum(x[m] * cmath.exp(-2j * cmath.pi * k * m / n) for m in range(n))
        for k in range(n)
    ]


def roundtrip(x, params):
    result = stft(x, params)
    back = istft(result.spectrogram, params, length=len(x))
    return back.samples


# ---------------------------------------------------------------- round trip


@pytest.mark.parametrize(
    "n_fft,win_length,hop",
    [
        (256, 256, 64),    # even window, 75% overlap
        (256, 256, 128),   # even window, 50% overlap (COLA for hann)
        (256, 255, 64),    # odd window length inside even n_fft
        (255, 255, 85),    # odd n_fft and window
        (128, 100, 25),    # window shorter than n_fft
        (64, 64, 1),       # hop 1
    ],
)
def test_roundtrip_random_signal(n_fft, win_length, hop):
    rng = np.random.default_rng(42)
    x = rng.standard_normal(1000)
    params = StftParams(n_fft=n_fft, win_length=win_length, hop_length=hop)
    back = roundtrip(x, params)
    assert back.shape == x.shape  # original length preserved
    np.testing.assert_allclose(back, x, atol=1e-9)


def test_roundtrip_no_overlap_requires_nonzero_window_endpoints():
    rng = np.random.default_rng(42)
    x = rng.standard_normal(1000)
    # hop == win_length == n_fft with rect: no overlap, but the window is
    # nonzero everywhere, so reconstruction is exact.
    rect = StftParams(n_fft=512, win_length=512, hop_length=512, window="rect")
    np.testing.assert_allclose(roundtrip(x, rect), x, atol=1e-9)
    # Same geometry with hann: frame-boundary samples sit exactly on the
    # window's zero endpoints, so the OLA denominator is zero there and the
    # backend must refuse to reconstruct.
    hann = StftParams(n_fft=512, win_length=512, hop_length=512)
    S = stft(x, hann).spectrogram
    with pytest.raises(NotReconstructibleError):
        istft(S, hann, length=len(x))


def test_roundtrip_lengths_including_single_sample():
    params = StftParams(n_fft=128, win_length=128, hop_length=32)
    rng = np.random.default_rng(7)
    for n in (1, 2, 63, 127, 128, 129, 1000):
        x = rng.standard_normal(n)
        back = roundtrip(x, params)
        assert back.shape == (n,)
        np.testing.assert_allclose(back, x, atol=1e-9)


def test_signal_shorter_than_one_window():
    # 100 samples with a 256-point window: must still reconstruct exactly.
    rng = np.random.default_rng(3)
    x = rng.standard_normal(100)
    params = StftParams(n_fft=256, win_length=256, hop_length=64)
    result = stft(x, params)
    assert result.n_frames >= 1
    back = istft(result.spectrogram, params, length=100)
    np.testing.assert_allclose(back.samples, x, atol=1e-9)


def test_boundary_impulses():
    # Energy exactly at sample 0 and at the last sample exercises the
    # centre-padding / crop boundaries.
    n = 1000
    params = StftParams(n_fft=256, win_length=256, hop_length=64)
    for pos in (0, n - 1):
        x = np.zeros(n)
        x[pos] = 1.0
        back = roundtrip(x, params)
        np.testing.assert_allclose(back, x, atol=1e-9)
        assert back[pos] == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------- frame index mapping


def test_frame_center_sample_mapping():
    assert frame_center_sample(0, 128) == 0
    assert frame_center_sample(5, 128) == 640
    params = StftParams(n_fft=256, win_length=256, hop_length=128)
    # Frame 2 is centred on sample 256 and spans [128, 384).
    assert frame_sample_span(2, params) == (128, 384)


def test_impulse_lands_in_expected_frame():
    # Impulse at sample 300, hop 128: nearest frame centre is 256 (k=2).
    n, hop = 1024, 128
    x = np.zeros(n)
    x[300] = 1.0
    params = StftParams(n_fft=256, win_length=256, hop_length=hop)
    result = stft(x, params)
    energy = np.sum(np.abs(result.spectrogram) ** 2, axis=1)
    peak_frame = int(np.argmax(energy))
    assert peak_frame == 2
    assert frame_center_sample(peak_frame, hop) == 256


# -------------------------------------------- hand-computed reference values


def test_stft_matches_hand_computed_rect_case():
    # rect window, n_fft=4, hop=4, x=[1,2,3,4].
    # Centre pad = 2 -> padded = [0,0,1,2,3,4,0,0]
    # frame 0 = [0,0,1,2] -> rfft = [3, -1+2j, -1]
    # frame 1 = [3,4,0,0] -> rfft = [7, 3-4j, -1]
    params = StftParams(n_fft=4, win_length=4, hop_length=4, window="rect")
    result = stft([1.0, 2.0, 3.0, 4.0], params)
    assert result.n_frames == 2
    np.testing.assert_allclose(
        result.spectrogram[0], [3 + 0j, -1 + 2j, -1 + 0j], atol=1e-12
    )
    np.testing.assert_allclose(
        result.spectrogram[1], [7 + 0j, 3 - 4j, -1 + 0j], atol=1e-12
    )


def test_stft_matches_naive_dft():
    # Cross-check the core against a from-definition DFT on windowed frames.
    rng = np.random.default_rng(11)
    x = rng.standard_normal(64)
    params = StftParams(n_fft=16, win_length=16, hop_length=8)
    result = stft(x, params)
    win = window_buffer(params)
    padded = np.concatenate([np.zeros(8), x, np.zeros(8)])
    for k in range(result.n_frames):
        frame = padded[k * 8 : k * 8 + 16] * win
        expected = naive_dft(frame)[: 16 // 2 + 1]  # one-sided part
        np.testing.assert_allclose(result.spectrogram[k], expected, atol=1e-10)


# ---------------------------------------------------------- onesided expand


def test_expand_restores_conjugate_symmetry_even_n_fft():
    rng = np.random.default_rng(5)
    x = rng.standard_normal(16)
    params = StftParams(n_fft=16, win_length=16, hop_length=16)
    result = stft(x, params)
    win = window_buffer(params)
    padded = np.concatenate([np.zeros(8), x, np.zeros(8)])
    frame = padded[0:16] * win
    reference = np.asarray(naive_dft(frame))  # full 16-bin reference

    full = onesided_to_full(result.spectrogram, n_fft=16)
    assert full.shape == (result.n_frames, 16)
    np.testing.assert_allclose(full[0], reference, atol=1e-10)
    # DC and Nyquist appear exactly once, at their own positions.
    assert full[0, 0] == result.spectrogram[0, 0]
    assert full[0, 8] == result.spectrogram[0, 8]
    # Symmetry: X[k] == conj(X[N-k]) for the mirrored bins.
    for k in range(1, 8):
        assert full[0, k] == pytest.approx(np.conj(full[0, 16 - k]))


def test_expand_odd_n_fft_has_no_nyquist_bin():
    x = np.arange(1.0, 8.0)  # 7 samples
    params = StftParams(n_fft=7, win_length=7, hop_length=7, window="rect")
    result = stft(x, params)
    assert result.spectrogram.shape[1] == 4  # (7+1)//2 bins, no Nyquist
    full = onesided_to_full(result.spectrogram, n_fft=7)
    assert full.shape[1] == 7
    frame = np.concatenate([np.zeros(3), x, np.zeros(3)])[:7]
    np.testing.assert_allclose(full[0], naive_dft(frame), atol=1e-10)


def test_expand_rejects_wrong_bin_count():
    S = np.zeros((2, 5), dtype=complex)
    with pytest.raises(ShapeMismatchError):
        onesided_to_full(S, n_fft=16)  # expects 9 bins, got 5


# --------------------------------------------------------- rejection paths


def test_hop_larger_than_window_is_rejected():
    params = StftParams(n_fft=256, win_length=128, hop_length=256)
    with pytest.raises(NotReconstructibleError):
        stft(np.zeros(100), params)
    with pytest.raises(NotReconstructibleError):
        istft(np.zeros((3, 129), dtype=complex), params, length=100)


def test_win_length_larger_than_n_fft_is_rejected():
    params = StftParams(n_fft=64, win_length=128, hop_length=16)
    with pytest.raises(ConfigError):
        stft(np.zeros(100), params)


def test_unknown_window_is_rejected():
    params = StftParams(n_fft=64, win_length=64, hop_length=16, window="kaiser")
    with pytest.raises(ConfigError):
        stft(np.zeros(100), params)


def test_empty_signal_is_rejected():
    params = StftParams(n_fft=64, win_length=64, hop_length=16)
    with pytest.raises(EmptySignalError):
        stft([], params)


def test_istft_rejects_mismatched_spectrum_shape():
    params = StftParams(n_fft=256, win_length=256, hop_length=64)
    # n_fft=256 implies 129 bins; give it 64.
    with pytest.raises(ShapeMismatchError):
        istft(np.zeros((4, 64), dtype=complex), params, length=100)


def test_istft_rejects_length_beyond_extent():
    params = StftParams(n_fft=64, win_length=64, hop_length=16)
    S = np.zeros((2, 33), dtype=complex)  # extent: 64 + 16 = 80 -> 48 usable
    with pytest.raises(ShapeMismatchError):
        istft(S, params, length=10_000)


def test_ola_denominator_check_flags_zeros():
    params = StftParams(n_fft=64, win_length=64, hop_length=16)
    den = ola_denominator(params, n_frames=4)
    assert den.shape == (64 + 3 * 16,)
    # The outermost pad samples of the extent legitimately have a ~0
    # denominator (hann endpoints are zero); the interior — where kept
    # samples live — must be strictly positive.
    assert np.all(den[params.pad : -params.pad] > 0)
    # A denominator with a hole must be rejected by the checker.
    den_with_hole = den.copy()
    den_with_hole[10] = 0.0
    with pytest.raises(NotReconstructibleError):
        check_ola_denominator(den_with_hole, params)


def test_window_buffer_centres_short_windows():
    params = StftParams(n_fft=8, win_length=4, hop_length=2, window="rect")
    w = window_buffer(params)
    np.testing.assert_array_equal(w, [0, 0, 1, 1, 1, 1, 0, 0])
    # Odd window length: centred with the extra zero on the right.
    params_odd = StftParams(n_fft=8, win_length=3, hop_length=2, window="rect")
    np.testing.assert_array_equal(
        window_buffer(params_odd), [0, 0, 1, 1, 1, 0, 0, 0]
    )
