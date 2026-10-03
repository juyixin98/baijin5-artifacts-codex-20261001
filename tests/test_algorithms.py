"""Numerical tests for the batch STFT/ISTFT core.

Every expected value comes from ``reference_oracle`` (DFT matrices,
closed-form constants or SciPy), never from the implementation under
test. Cases mandated by the spec are all present:

* even *and* odd window lengths;
* boundary impulses (pulse at sample 0 and at the last sample);
* a signal shorter than one window;
* mismatched spectrum shapes;
* frame-index -> sample-position mapping.
"""

from __future__ import annotations

import numpy as np
import pytest

from stft_backend import algorithms
from stft_backend.errors import ErrorCode, StftError
from stft_backend.numeric import validate_transform_params
from stft_backend.windows import resolve_window

from reference_oracle import (
    expected_frame_times_scipy,
    expected_hann4_hop2_reconstruction,
    make_window,
    oracle_istft_nfft_grid,
    oracle_stft,
)

pytestmark = pytest.mark.numerical

RTOL = 1e-10
ATOL = 1e-10


def _params(nperseg: int, hop: int, nfft: int | None = None,
            window: str = "hann", onesided: bool = True):
    return validate_transform_params(nperseg, hop, nfft, window) + (onesided,)


# ---------------------------------------------------------------- forward

@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3), (16, 7), (5, 2)])
def test_stft_matches_dft_matrix_oracle(nperseg: int, hop: int) -> None:
    rng = np.random.default_rng(20261003)
    x = rng.standard_normal(31)
    nperseg_, hop_, nfft_, window, _ = _params(nperseg, hop)
    got, meta = algorithms.stft(
        x, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window
    )
    want = oracle_stft(
        x, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window
    )
    assert got.shape == want.shape
    np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12)
    assert meta.input_length == 31


@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3)])
def test_stft_twosided_matches_oracle(nperseg: int, hop: int) -> None:
    rng = np.random.default_rng(7)
    x = rng.standard_normal(20)
    nperseg_, hop_, nfft_, window, _ = _params(nperseg, hop)
    got, meta = algorithms.stft(
        x, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window,
        onesided=False,
    )
    want = oracle_stft(
        x, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window,
        onesided=False,
    )
    np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12)
    assert got.shape[0] == nperseg_


def test_nfft_larger_than_nperseg_matches_oracle() -> None:
    rng = np.random.default_rng(11)
    x = rng.standard_normal(24)
    nperseg_, hop_, nfft_, window, _ = _params(8, 3, nfft=16)
    assert nfft_ == 16
    got, _ = algorithms.stft(
        x, nperseg=8, hop=3, nfft=16, window=window
    )
    want = oracle_stft(x, nperseg=8, hop=3, nfft=16, window=window)
    np.testing.assert_allclose(got, want, rtol=1e-12, atol=1e-12)
    assert got.shape[0] == 9


def test_onesided_bins_for_even_and_odd_nfft() -> None:
    x = np.arange(10, dtype=float)
    w8 = resolve_window("hann", 8)
    spec_even, _ = algorithms.stft(x, nperseg=8, hop=4, nfft=8, window=w8)
    assert spec_even.shape[0] == 5  # DC..Nyquist, Nyquist present once
    w7 = resolve_window("hann", 7)
    spec_odd, _ = algorithms.stft(x, nperseg=7, hop=3, nfft=7, window=w7)
    assert spec_odd.shape[0] == 4  # DC + 3 positive bins, no Nyquist bin


# --------------------------------------------------- frame index positions

@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3), (5, 2)])
def test_frame_positions_match_scipy_time_vector(nperseg: int, hop: int) -> None:
    n = 20
    w = resolve_window("hann", nperseg)
    _spec, meta = algorithms.stft(
        np.zeros(n), nperseg=nperseg, hop=hop, nfft=nperseg, window=w
    )
    want_centers = expected_frame_times_scipy(n, nperseg, hop)
    got_centers = np.array(
        [p["center_sample"] for p in meta.frame_positions()], dtype=float
    )
    np.testing.assert_allclose(got_centers, want_centers, rtol=0, atol=1e-12)
    assert len(got_centers) == meta.n_frames
    for p in meta.frame_positions():
        assert p["start_sample"] == p["frame_index"] * hop - nperseg // 2
        assert p["center_sample"] == p["frame_index"] * hop


# ----------------------------------------------------------- round trips

@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3), (16, 8), (5, 2),
                                         (64, 16), (9, 4)])
def test_roundtrip_recovers_signal_for_even_and_odd(nperseg: int, hop: int) -> None:
    rng = np.random.default_rng(42 + nperseg)
    x = rng.standard_normal(50)
    nperseg_, hop_, nfft_, window, _ = _params(nperseg, hop)
    spec, meta = algorithms.stft(
        x, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window
    )
    y = algorithms.istft(
        spec, nperseg=nperseg_, hop=hop_, nfft=nfft_, window=window,
        signal_length=meta.input_length,
    )
    assert y.shape == x.shape  # original length preserved
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)


def test_roundtrip_matches_independent_nfft_grid_oracle() -> None:
    rng = np.random.default_rng(99)
    x = rng.standard_normal(33)
    nperseg, hop, nfft, window = 8, 3, 8, make_window("hann", 8)
    spec, meta = algorithms.stft(
        x, nperseg=nperseg, hop=hop, nfft=nfft, window=window
    )
    # Independently rebuild the full spectrum straight from the oracle STFT.
    oracle_one = oracle_stft(
        x, nperseg=nperseg, hop=hop, nfft=nfft, window=window
    )
    mirrored = oracle_one[-2:0:-1, :]
    oracle_full = np.concatenate([oracle_one, np.conj(mirrored)], axis=0)
    want = oracle_istft_nfft_grid(
        oracle_full, nperseg=nperseg, hop=hop, nfft=nfft,
        window=window, length=x.size,
    )
    y = algorithms.istft(
        spec, nperseg=nperseg, hop=hop, nfft=nfft, window=window,
        signal_length=x.size,
    )
    np.testing.assert_allclose(y, want, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)


def test_closed_form_hann4_hop2_exact() -> None:
    """Exact w=[0,1,1,0], hop=2 case checked against hand derivation."""

    for n in (1, 2, 3, 4, 5, 7, 12):
        x = np.sin(np.arange(n) * 0.7) + 0.25 * np.arange(n)
        window = np.array([0.0, 1.0, 1.0, 0.0])
        spec, meta = algorithms.stft(
            x, nperseg=4, hop=2, nfft=4, window=window
        )
        y = algorithms.istft(
            spec, nperseg=4, hop=2, nfft=4, window=window,
            signal_length=meta.input_length,
        )
        want = expected_hann4_hop2_reconstruction(x)
        np.testing.assert_allclose(want, x, rtol=0, atol=1e-12)
        np.testing.assert_allclose(y, x, rtol=1e-12, atol=1e-12)
        assert y.shape == (n,)


# ------------------------------------------------------ mandated edge cases

@pytest.mark.parametrize("pulse_index,nperseg,hop", [
    (0, 8, 4), (0, 7, 3), (-1, 8, 4), (-1, 7, 3),
])
def test_boundary_impulses_roundtrip(pulse_index: int, nperseg: int,
                                     hop: int) -> None:
    n = 19
    x = np.zeros(n)
    x[pulse_index] = 1.0
    window = resolve_window("hann", nperseg)
    spec, meta = algorithms.stft(
        x, nperseg=nperseg, hop=hop, nfft=nperseg, window=window
    )
    y = algorithms.istft(
        spec, nperseg=nperseg, hop=hop, nfft=nperseg, window=window,
        signal_length=meta.input_length,
    )
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)
    # The pulse energy lands on exactly one output sample.
    assert np.count_nonzero(np.abs(y) > 1e-8) == 1
    assert int(np.argmax(np.abs(y))) == (n - 1 if pulse_index == -1 else 0)


@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3), (5, 2)])
def test_signal_shorter_than_one_window(nperseg: int, hop: int) -> None:
    x = np.array([1.0, -0.5, 0.25])
    assert x.size < nperseg
    window = resolve_window("hann", nperseg)
    spec, meta = algorithms.stft(
        x, nperseg=nperseg, hop=hop, nfft=nperseg, window=window
    )
    # All samples still fall inside the first frame's window support.
    assert meta.n_frames >= 1
    y = algorithms.istft(
        spec, nperseg=nperseg, hop=hop, nfft=nperseg, window=window,
        signal_length=x.size,
    )
    assert y.size == x.size
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)


def test_original_length_is_preserved_exactly() -> None:
    rng = np.random.default_rng(3)
    for n in (1, 2, 6, 13, 17, 40):
        x = rng.standard_normal(n)
        window = resolve_window("hann", 8)
        spec, _ = algorithms.stft(x, nperseg=8, hop=3, nfft=8, window=window)
        y = algorithms.istft(
            spec, nperseg=8, hop=3, nfft=8, window=window, signal_length=n
        )
        assert y.shape == (n,)


# -------------------------------------------------- spectrum shape failures

def test_wrong_frequency_bin_count_is_rejected() -> None:
    x = np.arange(16, dtype=float)
    window = resolve_window("hann", 8)
    spec, _ = algorithms.stft(x, nperseg=8, hop=4, nfft=8, window=window)
    bad = spec[:-1, :]  # 4 bins instead of 5
    with pytest.raises(StftError) as excinfo:
        algorithms.istft(
            bad, nperseg=8, hop=4, nfft=8, window=window, signal_length=16
        )
    assert excinfo.value.code is ErrorCode.SPECTRUM_SHAPE_MISMATCH
    assert excinfo.value.details["expected_freq_bins"] == 5
    assert excinfo.value.details["nfft"] == 8
    assert excinfo.value.stage == "spectrum_validation"


def test_one_dimension_spectrum_is_rejected() -> None:
    window = resolve_window("hann", 8)
    with pytest.raises(StftError) as excinfo:
        algorithms.istft(
            np.zeros(5, dtype=np.complex128),
            nperseg=8, hop=4, nfft=8, window=window,
        )
    assert excinfo.value.code is ErrorCode.SPECTRUM_SHAPE_MISMATCH


def test_empty_frames_rejected() -> None:
    window = resolve_window("hann", 8)
    with pytest.raises(StftError) as excinfo:
        algorithms.istft(
            np.zeros((5, 0), dtype=np.complex128),
            nperseg=8, hop=4, nfft=8, window=window,
        )
    assert excinfo.value.code is ErrorCode.SPECTRUM_SHAPE_MISMATCH


def test_signal_length_beyond_coverage_is_rejected() -> None:
    """Zero OLA denominator in the requested region -> explicit failure."""

    x = np.zeros(10)
    window = resolve_window("hann", 8)
    spec, _ = algorithms.stft(x, nperseg=8, hop=4, nfft=8, window=window)
    # The available frames only cover 10 original samples; ask for 40.
    with pytest.raises(StftError) as excinfo:
        algorithms.istft(
            spec, nperseg=8, hop=4, nfft=8, window=window, signal_length=40
        )
    err = excinfo.value
    assert err.code is ErrorCode.UNCOVERED_SAMPLES
    assert err.stage == "ola_normalize"
    assert err.details["first_uncovered_sample"] >= 10
    assert err.details["requested_length"] == 40


def test_non_finite_signal_rejected() -> None:
    window = resolve_window("hann", 8)
    with pytest.raises(StftError) as excinfo:
        algorithms.stft(
            np.array([1.0, float("nan"), 3.0]),
            nperseg=8, hop=4, nfft=8, window=window,
        )
    assert excinfo.value.code is ErrorCode.NON_FINITE_SIGNAL


# ------------------------------------------------------- symmetry recovery

def test_hermitian_recovery_does_not_duplicate_dc_or_nyquist() -> None:
    x = np.array([1.0, 0.5, -0.5, -1.0, 0.25, 0.75])
    window = resolve_window("hann", 8)
    spec, _ = algorithms.stft(x, nperseg=8, hop=4, nfft=8, window=window)
    # DC and Nyquist are single real rows of the one-sided spectrum.
    assert spec.shape[0] == 5
    np.testing.assert_allclose(spec[0, :].imag, 0.0, atol=1e-12)
    np.testing.assert_allclose(spec[-1, :].imag, 0.0, atol=1e-12)
    y = algorithms.istft(
        spec, nperseg=8, hop=4, nfft=8, window=window, signal_length=6
    )
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)


def test_non_real_dc_bin_is_rejected() -> None:
    window = resolve_window("hann", 8)
    bad = np.zeros((5, 3), dtype=np.complex128)
    bad[0, 1] = 0.0 + 1.0j  # imaginary DC cannot come from a real signal
    with pytest.raises(StftError) as excinfo:
        algorithms.istft(
            bad, nperseg=8, hop=4, nfft=8, window=window, signal_length=8
        )
    assert excinfo.value.code is ErrorCode.ASYMMETRIC_SPECTRUM
    assert excinfo.value.details["bin"] == "dc"


def test_odd_nfft_roundtrip_and_recovery() -> None:
    rng = np.random.default_rng(5)
    x = rng.standard_normal(21)
    window = resolve_window("hann", 7)
    spec, _ = algorithms.stft(x, nperseg=7, hop=3, nfft=7, window=window)
    assert spec.shape[0] == 4
    y = algorithms.istft(
        spec, nperseg=7, hop=3, nfft=7, window=window, signal_length=21
    )
    np.testing.assert_allclose(y, x, rtol=RTOL, atol=ATOL)


def test_explicit_window_samples_roundtrip() -> None:
    rng = np.random.default_rng(8)
    x = rng.standard_normal(30)
    window = make_window("hamming", 12)
    spec, _ = algorithms.stft(x, nperseg=12, hop=4, nfft=12, window=window)
    y = algorithms.istft(
        spec, nperseg=12, hop=4, nfft=12, window=window, signal_length=30
    )
    np.testing.assert_allclose(y, x, rtol=1e-9, atol=1e-9)


def test_error_summary_reports_concrete_metrics() -> None:
    x = np.array([1.0, 2.0, 3.0])
    y = x + 0.01
    metrics = algorithms.roundtrip_error(x, y)
    assert metrics["length_preserved"] is True
    assert metrics["max_abs_error"] == pytest.approx(0.01)
    assert metrics["rmse"] == pytest.approx(0.01)
