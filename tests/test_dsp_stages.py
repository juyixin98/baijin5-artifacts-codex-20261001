"""Stage-level numeric tests with hand-computed or independently derived
expected values — the guards against "normal input looks right, edge input
silently wrong"."""

import math

import numpy as np
import pytest

from mfcc_backend.config import MFCCConfig
from mfcc_backend.dsp import (
    build_mel_filterbank,
    fft_frequencies,
    frame_signal,
    hamming_window,
    hz_to_mel,
    mel_to_hz,
    power_spectrum,
    preemphasis,
)
from mfcc_backend.dsp.cepstrum import log_mel_spectrum, mfcc_from_log_mel
from mfcc_backend.errors import EmptyFilterSupportError, InsufficientSignalError

import reference_impl as ref


# -- pre-emphasis -------------------------------------------------------------

def test_preemphasis_known_vector(run_log):
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = preemphasis(x, 0.97)
    # hand-computed: y0 = x0 (x[-1] = 0); yn = xn - 0.97 * x_{n-1}
    expected = np.array([1.0, 2.0 - 0.97 * 1.0, 3.0 - 0.97 * 2.0, 4.0 - 0.97 * 3.0])
    np.testing.assert_allclose(y, expected, rtol=0, atol=1e-15)
    assert y[0] == x[0]  # boundary rule: first sample untouched
    run_log("preemphasis_boundary", expected=expected.tolist(), actual=y.tolist(),
            verdict="pass", rationale="x[-1]=0 boundary must keep y[0]==x[0]")


def test_preemphasis_matches_reference_on_noise():
    rng = np.random.default_rng(42)
    x = rng.standard_normal(1000)
    np.testing.assert_array_equal(preemphasis(x, 0.97), ref.preemphasis(x, 0.97))


# -- framing -------------------------------------------------------------------

def test_framing_count_content_and_tail_drop(run_log):
    x = np.arange(1000, dtype=np.float64)
    frames = frame_signal(x, frame_length=400, hop_length=160)
    # 1 + (1000-400)//160 = 4 frames; the 120-sample tail is dropped, not padded
    assert frames.shape == (4, 400)
    w = hamming_window(400)
    for i in range(4):
        np.testing.assert_array_equal(frames[i], x[i * 160 : i * 160 + 400] * w)
    run_log("framing", n_samples=1000, n_frames=frames.shape[0],
            dropped_tail=1000 - (3 * 160 + 400), verdict="pass",
            rationale="n_frames = 1 + (n - fl)//hop; partial tail dropped")


def test_hamming_matches_closed_form():
    w = hamming_window(400)
    expected = np.array([0.54 - 0.46 * math.cos(2 * math.pi * i / 399) for i in range(400)])
    np.testing.assert_allclose(w, expected, rtol=0, atol=1e-15)
    assert w[0] == pytest.approx(0.08)  # symmetric, not periodic


def test_too_short_signal_raises_specific_error(run_log):
    with pytest.raises(InsufficientSignalError) as excinfo:
        frame_signal(np.zeros(100), frame_length=400, hop_length=160)
    assert excinfo.value.code == "INSUFFICIENT_SIGNAL"
    assert excinfo.value.detail["n_samples"] == 100
    run_log("too_short_rejected", error_code=excinfo.value.code,
            detail=excinfo.value.detail, verdict="pass",
            rationale="sub-frame input must fail, not return an empty matrix")


# -- spectrum -------------------------------------------------------------------

def test_fft_frequency_axis_derives_from_sample_rate():
    freqs = fft_frequencies(16000, 400)
    assert freqs[0] == 0.0
    assert freqs[1] == pytest.approx(40.0)  # 16000/400
    assert freqs[-1] == pytest.approx(8000.0)  # Nyquist
    assert len(freqs) == 201


def test_power_spectrum_tone_lands_on_expected_bin(run_log):
    sr, nfft, k = 16000, 400, 10  # tone exactly on bin 10 -> 400 Hz
    n = np.arange(nfft)
    frame = np.cos(2 * np.pi * k * n / nfft)[None, :]
    power = power_spectrum(frame, nfft)[0]
    assert int(np.argmax(power)) == k
    # cosine amplitude 1 -> power 1/4 per mirrored bin, divided by nfft: |X|^2/nfft
    np.testing.assert_allclose(power[k], ref.power_spectrum(frame, nfft)[0][k],
                               rtol=1e-12, atol=1e-15)
    run_log("tone_bin", bin=k, power=float(power[k]), verdict="pass",
            rationale="frequency axis f=k*sr/nfft places a 400Hz tone at bin 10")


# -- mel filterbank ---------------------------------------------------------------

def test_mel_scale_roundtrip():
    for f in (0.0, 20.0, 1000.0, 8000.0):
        assert mel_to_hz(hz_to_mel(f)) == pytest.approx(f, rel=1e-12)


def test_filterbank_shape_support_and_alignment(run_log):
    cfg = MFCCConfig().validate()
    bank = build_mel_filterbank(cfg)
    assert bank.shape == (26, 201)
    assert np.all(bank >= 0.0)
    assert np.all(bank.sum(axis=1) > 0.0)  # no empty filters at this resolution
    np.testing.assert_allclose(bank, ref.mel_filterbank(16000, 400, 26, 20.0, 8000.0),
                               rtol=1e-12, atol=1e-15)
    # each filter peaks near its own mel-centre frequency
    freqs = fft_frequencies(cfg.sample_rate, cfg.nfft)
    for m in (0, 13, 25):
        peak_hz = freqs[int(np.argmax(bank[m]))]
        assert cfg.fmin_hz <= peak_hz <= cfg.fmax
    run_log("filterbank", shape=list(bank.shape), verdict="pass",
            rationale="triangles on the k*sr/nfft axis, no empty rows at 16kHz/400")


def test_empty_filter_support_detected(run_log):
    # 1 ms frame @ 8 kHz -> nfft=8, 5 bins for 26 mel filters -> many empty
    cfg = MFCCConfig(sample_rate=8000, frame_length_ms=1.0, hop_length_ms=0.5)
    with pytest.raises(EmptyFilterSupportError) as excinfo:
        build_mel_filterbank(cfg)
    err = excinfo.value
    assert err.code == "EMPTY_FILTER_SUPPORT"
    assert err.detail["empty_filters"], "error must name the offending filters"
    assert err.http_status == 422
    run_log("empty_support_detected", empty_filters=err.detail["empty_filters"],
            bin_spacing_hz=err.detail["bin_spacing_hz"], verdict="pass",
            rationale="all-zero mel rows must be a config error, not silent log(floor)")


# -- log floor + DCT ---------------------------------------------------------------

def test_log_floor_makes_silence_deterministic(run_log):
    cfg = MFCCConfig().validate()
    mel_e = np.zeros((3, cfg.n_mels))
    log_mel = log_mel_spectrum(mel_e, cfg.log_floor)
    np.testing.assert_array_equal(log_mel, np.full((3, cfg.n_mels), math.log(1e-10)))
    mfcc = mfcc_from_log_mel(log_mel, cfg.n_mfcc)
    # constant vector under orthonormal DCT-II: c0 = sqrt(M) * value, rest 0
    expected_c0 = math.sqrt(cfg.n_mels) * math.log(1e-10)
    np.testing.assert_allclose(mfcc[:, 0], expected_c0, rtol=0, atol=1e-12)
    np.testing.assert_allclose(mfcc[:, 1:], 0.0, rtol=0, atol=1e-12)
    run_log("silence_deterministic", expected_c0=expected_c0,
            actual_c0=float(mfcc[0, 0]), verdict="pass",
            rationale="log floor 1e-10 + ortho DCT => c0=sqrt(26)*ln(1e-10), ck>0=0")


def test_negative_mel_energy_rejected():
    with pytest.raises(ValueError):
        log_mel_spectrum(np.array([[-1.0]]), 1e-10)
