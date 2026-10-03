"""Unit tests for parameter validation, windows and the NOLA check."""

from __future__ import annotations

import numpy as np
import pytest

from stft_backend.errors import ErrorCode, StftError
from stft_backend.numeric import (
    nola_diagnostics,
    nola_period_denominator,
    validate_scalar_parameters,
    validate_transform_params,
)
from stft_backend.windows import resolve_window

pytestmark = pytest.mark.unit


def test_nperseg_must_be_at_least_two() -> None:
    with pytest.raises(StftError) as excinfo:
        validate_scalar_parameters(nperseg=1, hop=1, nfft=1)
    assert excinfo.value.code is ErrorCode.INVALID_PARAMETER
    assert excinfo.value.stage == "parameters"


def test_hop_zero_and_gap_hops_are_rejected() -> None:
    for bad_hop in (0, 8, 12):
        with pytest.raises(StftError) as excinfo:
            validate_scalar_parameters(nperseg=8, hop=bad_hop, nfft=8)
        assert excinfo.value.code is ErrorCode.INVALID_PARAMETER
        assert "hop" in excinfo.value.message


def test_nfft_smaller_than_nperseg_is_rejected() -> None:
    with pytest.raises(StftError) as excinfo:
        validate_scalar_parameters(nperseg=16, hop=8, nfft=8)
    assert excinfo.value.code is ErrorCode.NFFT_TOO_SMALL
    assert excinfo.value.details == {"nfft": 8, "nperseg": 16}


def test_boolean_is_not_accepted_as_integer() -> None:
    with pytest.raises(StftError) as excinfo:
        validate_scalar_parameters(nperseg=True, hop=4, nfft=8)  # type: ignore[arg-type]
    assert excinfo.value.code is ErrorCode.INVALID_PARAMETER


def test_explicit_window_length_mismatch() -> None:
    with pytest.raises(StftError) as excinfo:
        resolve_window([1.0, 0.0, 1.0], nperseg=4)
    assert excinfo.value.code is ErrorCode.WINDOW_LENGTH_MISMATCH
    assert excinfo.value.details["window_length"] == 3
    assert excinfo.value.details["nperseg"] == 4


def test_unsupported_window_name() -> None:
    with pytest.raises(StftError) as excinfo:
        resolve_window("not-a-real-window", nperseg=8)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_WINDOW


def test_non_finite_window_samples_rejected() -> None:
    with pytest.raises(StftError) as excinfo:
        resolve_window([1.0, float("nan"), 0.0, 1.0], nperseg=4)
    assert excinfo.value.code is ErrorCode.INVALID_PARAMETER


def test_zero_window_fails_nola_with_concrete_bins() -> None:
    window = np.zeros(8)
    with pytest.raises(StftError) as excinfo:
        nola_diagnostics(window, hop=4, nfft=8)
    err = excinfo.value
    assert err.code is ErrorCode.NOLA_VIOLATION
    assert err.stage == "nola_check"
    # The folded period has one entry per hop residue.
    assert err.details["zero_bin_count"] == 4
    assert err.details["zero_bins_first"] == [0, 1, 2, 3]
    assert err.details["min_denominator"] == 0.0


def test_sparse_window_fails_nola() -> None:
    # Only one non-zero tap: hop=4 leaves zero gaps between supports.
    window = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    with pytest.raises(StftError) as excinfo:
        validate_transform_params(8, 4, 8, window)
    assert excinfo.value.code is ErrorCode.NOLA_VIOLATION
    # Bin 0 receives only w[0]^2 = 0; the zero positions are reported.
    assert 0 in excinfo.value.details["zero_bins_first"]


def test_boxcar_with_hopping_gaps_fails_nola() -> None:
    # Rectangular window and hop that shifts its zero edge onto samples
    # whose only covering weight is that zero edge.
    window = resolve_window("hann", 8)
    window = window * 0.0
    window[2:6] = 1.0
    with pytest.raises(StftError) as excinfo:
        nola_diagnostics(window, hop=6, nfft=8)
    assert excinfo.value.code is ErrorCode.NOLA_VIOLATION


def test_hann_half_overlap_passes_nola_for_even_and_odd() -> None:
    for nperseg in (8, 7):
        window = resolve_window("hann", nperseg)
        diag = nola_diagnostics(window, hop=nperseg // 2, nfft=nperseg)
        assert diag["condition"] == "NOLA"
        assert diag["min_denominator"] > diag["tolerance"]


def test_nola_period_is_circular_energy_fold() -> None:
    # Hand-computed: w=[0,1,1,0], hop=2 folds to [1,1] per residue.
    window = np.array([0.0, 1.0, 1.0, 0.0])
    period = nola_period_denominator(window, hop=2)
    np.testing.assert_allclose(period, [1.0, 1.0], atol=1e-14)


def test_nola_fold_agrees_with_scipy_check_nola() -> None:
    from scipy.signal import check_NOLA

    rng = np.random.default_rng(0)
    cases = [(8, 3), (8, 4), (7, 2), (16, 5), (12, 6), (9, 4)]
    for nperseg, hop in cases:
        window = resolve_window("hann", nperseg)
        period = nola_period_denominator(window, hop)
        assert bool(period.min() > 1e-10) == bool(
            check_NOLA(window, nperseg, nperseg - hop)
        )
    # A random pathological sparse window must agree too.
    w = np.where(rng.integers(0, 2, size=32) == 1, 1.0, 0.0)
    try:
        verdict = check_NOLA(w, 32, 16)
    except ValueError:  # pragma: no cover - defensive
        verdict = False
    period = nola_period_denominator(w, 16)
    assert bool(period.min() > 1e-10) == bool(verdict)
