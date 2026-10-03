"""End-to-end pipeline numerics against analytic forms and the reference."""

import math

import numpy as np
import pytest

from mfcc_backend import DEFAULT_CONFIG, MFCCConfig, extract_features
from mfcc_backend.pipeline import dct_ii_ortho, log_mel_spectrogram

from reference_impl import ref_log_mel, ref_mfcc


def test_silence_hits_log_floor_analytically(silence, tlog):
    x, sr, input_id = silence
    result = extract_features(x, MFCCConfig(sample_rate=sr))
    floor = DEFAULT_CONFIG.log_floor
    # Every mel energy is 0 -> floored -> log_mel == log(floor) everywhere.
    # DCT-II/ortho of a constant row: c0 = sqrt(n_mels) * value, cj>0 = 0.
    want_c0 = math.sqrt(DEFAULT_CONFIG.n_mels) * math.log(floor)
    tlog.step("silence_analytic", input_id=input_id,
              basis="c0=sqrt(26)*ln(1e-10); c1..c12=0; delta=delta2=0",
              want_c0=want_c0, got_c0=float(result.mfcc[0, 0]),
              max_cj=float(np.max(np.abs(result.mfcc[:, 1:]))))
    assert result.log_mel.shape == (98, 26)
    np.testing.assert_allclose(result.log_mel, math.log(floor), atol=1e-12)
    np.testing.assert_allclose(result.mfcc[:, 0], want_c0, atol=1e-9)
    np.testing.assert_allclose(result.mfcc[:, 1:], 0.0, atol=1e-9)
    np.testing.assert_allclose(result.delta, 0.0, atol=1e-12)
    np.testing.assert_allclose(result.delta_delta, 0.0, atol=1e-12)


def test_sine_energy_concentrates_in_expected_mel_band(sine_440, tlog):
    x, sr, input_id = sine_440
    result = extract_features(x, MFCCConfig(sample_rate=sr))
    # 440 Hz sits between mel points covering ~387-484 Hz; the log-mel
    # spectrum of interior frames must peak at the filter whose center is
    # nearest 440 Hz.
    from mfcc_backend.filterbank import mel_filter_points

    pts = mel_filter_points(result.config)
    centers = pts[1:-1]
    want_band = int(np.argmin(np.abs(centers - 440.0)))
    interior = result.log_mel[10:-10]
    got_band = int(np.argmax(interior.mean(axis=0)))
    tlog.step("sine_band", input_id=input_id,
              basis="argmax of mean log-mel == filter nearest 440 Hz",
              want_band=want_band, got_band=got_band,
              center_hz=float(centers[want_band]))
    assert got_band == want_band
    # Far-from-peak bands sit at numerical leakage level and fluctuate in
    # the log domain, so global stationarity is not a valid check for a
    # non-hop-synchronous tone.  A 1000 Hz tone *is* hop-synchronous at
    # 16 kHz / 160-sample hop (exactly 10 cycles per hop), so its interior
    # frames must be near-identical.
    t = np.arange(sr) / sr
    sync = extract_features(np.sin(2.0 * np.pi * 1000.0 * t),
                            MFCCConfig(sample_rate=sr))
    interior_sync = sync.log_mel[10:-10]
    frame_std = float(np.std(interior_sync, axis=0).max())
    tlog.step("sine_stationarity", input_id="sine_1000hz_hop_synchronous",
              basis="per-band std of interior log-mel rows < 1e-6 for a "
              "hop-synchronous tone",
              max_std=frame_std)
    assert frame_std < 1e-6


def test_noise_intermediates_match_reference(white_noise, tlog):
    x, sr, input_id = white_noise
    # Small config so the literal-loop reference stays fast.
    cfg = MFCCConfig(sample_rate=sr, n_fft=256, window_ms=8.0, hop_ms=4.0,
                     n_mels=10, n_mfcc=5)
    segment = x[:2048]
    result = extract_features(segment, cfg)
    want_log_mel = np.array(ref_log_mel(
        segment.tolist(), sample_rate=sr, preemphasis=cfg.preemphasis,
        frame_length=cfg.frame_length, hop=cfg.hop_length, n_fft=cfg.n_fft,
        n_mels=cfg.n_mels, fmin=cfg.fmin, fmax=cfg.resolved_fmax,
        log_floor=cfg.log_floor))
    want_mfcc = np.array(ref_mfcc(want_log_mel.tolist(), cfg.n_mfcc))
    tlog.step("noise_vs_reference", input_id=input_id,
              basis="log-mel and mfcc vs independent literal implementation",
              n_frames=result.n_frames,
              log_mel_max_err=float(np.max(np.abs(result.log_mel - want_log_mel))),
              mfcc_max_err=float(np.max(np.abs(result.mfcc - want_mfcc))))
    assert result.log_mel.shape == want_log_mel.shape
    np.testing.assert_allclose(result.log_mel, want_log_mel, atol=1e-8)
    np.testing.assert_allclose(result.mfcc, want_mfcc, atol=1e-8)


def test_dimensions_and_frame_counts(short_input, sine_440, tlog):
    x, sr, input_id = sine_440
    result = extract_features(x, MFCCConfig(sample_rate=sr))
    tlog.step("dimensions", input_id=input_id,
              basis="1s@16k -> 98 frames; mfcc/delta/delta2 (98,13)",
              n_frames=result.n_frames, mfcc_shape=result.mfcc.shape)
    assert result.n_frames == 98
    for block in (result.mfcc, result.delta, result.delta_delta):
        assert block.shape == (98, 13)
    assert result.power.shape == (98, 257)
    assert result.frames.shape == (98, 400)

    xs, srs, short_id = short_input
    short = extract_features(xs, MFCCConfig(sample_rate=srs))
    tlog.step("short_input", input_id=short_id,
              basis="120 samples < 400 -> 0 frames, shapes (0, 13), no exception",
              n_frames=short.n_frames)
    assert short.n_frames == 0
    assert short.mfcc.shape == (0, 13)
    assert short.delta.shape == (0, 13)
    assert short.delta_delta.shape == (0, 13)


def test_same_params_same_bits_across_calls(white_noise, tlog):
    x, sr, input_id = white_noise
    cfg = MFCCConfig(sample_rate=sr)
    a = extract_features(x, cfg)
    b = extract_features(x.copy(), cfg)
    tlog.step("cross_batch_identical", input_id=input_id,
              basis="same input + same config -> bitwise identical features")
    np.testing.assert_array_equal(a.mfcc, b.mfcc)
    np.testing.assert_array_equal(a.delta, b.delta)
    np.testing.assert_array_equal(a.delta_delta, b.delta_delta)


def test_log_floor_guards_tiny_energies(tlog):
    # A nonzero-but-tiny signal must never produce -inf log-mel.
    sr = 16000
    x = np.full(1600, 1e-12)
    result = extract_features(x, MFCCConfig(sample_rate=sr))
    tlog.step("log_floor", basis="no -inf/nan; all log-mel >= ln(log_floor)",
              min_log_mel=float(result.log_mel.min()),
              floor=math.log(DEFAULT_CONFIG.log_floor))
    assert np.all(np.isfinite(result.log_mel))
    assert np.all(result.log_mel >= math.log(DEFAULT_CONFIG.log_floor) - 1e-9)


def test_dct_orthogonality(tlog):
    rng = np.random.default_rng(0)
    rows = rng.standard_normal((7, 26))
    coeffs = dct_ii_ortho(rows, 26)
    tlog.step("dct_ortho", basis="ortho DCT-II preserves energy (Parseval)",
              err=float(abs(np.sum(rows**2) - np.sum(coeffs**2))))
    assert np.sum(rows**2) == pytest.approx(np.sum(coeffs**2), rel=1e-10)
