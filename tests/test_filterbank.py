"""Mel filterbank: scale anchors, frequency axis, empty-support detection."""

import numpy as np
import pytest

from mfcc_backend import EmptyFilterError, MFCCConfig
from mfcc_backend.filterbank import (
    build_mel_filterbank,
    fft_frequencies,
    hz_to_mel,
    mel_filter_points,
    mel_to_hz,
)

from reference_impl import ref_filterbank, ref_hz_to_mel, ref_mel_to_hz


def test_mel_scale_anchors(tlog):
    tlog.step("mel_anchors", basis="HTK: mel(0)=0, mel(1000)=2595*log10(1+1000/700)",
              mel_0=float(hz_to_mel(0.0)), mel_1000=float(hz_to_mel(1000.0)))
    assert hz_to_mel(0.0) == pytest.approx(0.0)
    assert hz_to_mel(1000.0) == pytest.approx(2595.0 * np.log10(1.0 + 1000.0 / 700.0))
    assert mel_to_hz(hz_to_mel(3123.4)) == pytest.approx(3123.4)
    assert ref_hz_to_mel(1000.0) == pytest.approx(float(hz_to_mel(1000.0)))
    assert ref_mel_to_hz(999.0) == pytest.approx(float(mel_to_hz(999.0)))


def test_frequency_axis_is_sample_rate_determined(tlog):
    cfg16 = MFCCConfig(sample_rate=16000)
    cfg8 = MFCCConfig(sample_rate=8000)
    f16 = fft_frequencies(cfg16)
    f8 = fft_frequencies(cfg8)
    tlog.step("freq_axis", basis="bin k at k*sr/n_fft; axis changes with sr",
              f16_bin1=float(f16[1]), f8_bin1=float(f8[1]),
              f16_last=float(f16[-1]), f8_last=float(f8[-1]))
    assert f16[1] == pytest.approx(16000 / 512)
    assert f8[1] == pytest.approx(8000 / 512)
    assert f16[-1] == pytest.approx(8000.0)   # Nyquist at 16 kHz
    assert f8[-1] == pytest.approx(4000.0)    # Nyquist at 8 kHz
    # Same config at 8 kHz: the top filters sit above 4 kHz Nyquist -> fmax
    # resolves to 4000 and the filter points move accordingly.
    pts16 = mel_filter_points(cfg16)
    pts8 = mel_filter_points(cfg8)
    assert pts16[-1] == pytest.approx(8000.0)
    assert pts8[-1] == pytest.approx(4000.0)


def test_filterbank_matches_reference(sine_440, tlog):
    _, sr, input_id = sine_440
    cfg = MFCCConfig(sample_rate=sr)
    got = build_mel_filterbank(cfg)
    want = np.array(ref_filterbank(cfg.n_mels, cfg.n_fft, sr, cfg.fmin,
                                   cfg.resolved_fmax))
    tlog.step("filterbank_vs_reference", input_id=input_id,
              basis="literal per-bin triangle evaluation, atol=1e-12",
              shape=got.shape, max_abs_err=float(np.max(np.abs(got - want))))
    assert got.shape == (26, 257)
    np.testing.assert_allclose(got, want, atol=1e-12)


def test_filterbank_rows_are_triangles_peaking_near_one(tlog):
    fb = build_mel_filterbank(MFCCConfig())
    tlog.step("triangle_shape", basis="each row in [0,1], single peak; sampled "
              "peak < 1 allowed when the center falls between FFT bins",
              row_max=[float(fb[m].max()) for m in (0, 13, 25)])
    assert np.all(fb >= 0.0)
    assert np.all(fb <= 1.0 + 1e-12)
    for m in range(fb.shape[0]):
        row = fb[m]
        peak = int(np.argmax(row))
        # The triangle's exact peak of 1.0 sits at the center frequency; the
        # sampled maximum is lower when no FFT bin lands on the center.
        assert row[peak] > 0.5
        # non-decreasing up to the peak, non-increasing after it
        assert np.all(np.diff(row[: peak + 1]) >= -1e-12)
        assert np.all(np.diff(row[peak:]) <= 1e-12)


def test_empty_support_filters_are_detected(tlog):
    # 64-point FFT at 8 kHz -> 33 bins over 4 kHz; 40 mel filters cannot all
    # find support.  This must fail loudly, not silently floor to log(0).
    cfg = MFCCConfig(sample_rate=8000, n_fft=64, window_ms=8.0, hop_ms=4.0,
                     n_mels=40, n_mfcc=13)
    tlog.step("empty_support", basis="EmptyFilterError, category filterbank_empty_support",
              n_fft=64, n_mels=40, sample_rate=8000)
    with pytest.raises(EmptyFilterError) as excinfo:
        build_mel_filterbank(cfg)
    err = excinfo.value
    assert err.category == "filterbank_empty_support"
    assert err.details["empty_filters"], "must list the offending filter indices"
    tlog.step("empty_support_detected", basis="error lists empty filter indices",
              empty_filters=err.details["empty_filters"])


def test_degenerate_band_edges_rejected(tlog):
    # fmin == fmax collapses the band; config validation must reject it.
    from mfcc_backend import ConfigError

    with pytest.raises(ConfigError) as excinfo:
        build_mel_filterbank(MFCCConfig(fmin=4000.0, fmax=4000.0))
    tlog.step("degenerate_band", basis="fmin==fmax -> ConfigError before building",
              exc_type=type(excinfo.value).__name__)
