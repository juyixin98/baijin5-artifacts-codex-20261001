"""Full-pipeline cross-checks against the independent reference
implementation, on sine / noise / silence / one-frame inputs, plus
same-parameter cross-batch determinism."""

import numpy as np
import pytest

from mfcc_backend import fixtures
from mfcc_backend.config import MFCCConfig
from mfcc_backend.pipeline import compute_pipeline

import reference_impl as ref
from conftest import signal_id

CFG = MFCCConfig().validate()
TOL = dict(rtol=1e-9, atol=1e-10)

SIGNALS = {
    "sine_440": fixtures.make_sine(440.0, duration_s=0.5),
    "sine_977_offbin": fixtures.make_sine(977.0, duration_s=0.5),
    "white_noise_seed7": fixtures.make_white_noise(duration_s=0.5, seed=7),
    "silence": fixtures.make_silence(duration_s=0.5),
    "one_frame": fixtures.make_one_frame(),
}


@pytest.mark.parametrize("kind", sorted(SIGNALS))
def test_intermediates_match_reference(kind, run_log):
    x = SIGNALS[kind]
    got = compute_pipeline(x, CFG)
    want = ref.mfcc_pipeline(
        x,
        sample_rate=CFG.sample_rate,
        frame_length=CFG.frame_length,
        hop=CFG.hop_length,
        preemph=CFG.preemphasis_coef,
        n_mels=CFG.n_mels,
        n_mfcc=CFG.n_mfcc,
        fmin=CFG.fmin_hz,
        fmax=CFG.fmax,
        log_floor=CFG.log_floor,
        delta_width=CFG.delta_width,
    )
    # frame count formula pinned: 1 + (n - 400)//160
    assert got.n_frames == 1 + (len(x) - CFG.frame_length) // CFG.hop_length
    assert got.mfcc.shape == (got.n_frames, CFG.n_mfcc)
    assert got.delta.shape == got.mfcc.shape
    assert got.delta2.shape == got.mfcc.shape

    np.testing.assert_allclose(got.preemphasized, want["preemphasized"], **TOL)
    np.testing.assert_allclose(got.frames, want["frames"], **TOL)
    np.testing.assert_allclose(got.power, want["power"], **TOL)
    np.testing.assert_allclose(got.mel_energies, want["mel_energies"], **TOL)
    np.testing.assert_allclose(got.log_mel, want["log_mel"], **TOL)
    np.testing.assert_allclose(got.mfcc, want["mfcc"], **TOL)
    np.testing.assert_allclose(got.delta, want["delta"], **TOL)
    np.testing.assert_allclose(got.delta2, want["delta2"], **TOL)

    run_log(
        "pipeline_vs_reference",
        input_id=signal_id(x),
        kind=kind,
        n_frames=got.n_frames,
        max_abs_diff_mfcc=float(np.max(np.abs(got.mfcc - want["mfcc"]))),
        max_abs_diff_delta2=float(np.max(np.abs(got.delta2 - want["delta2"]))),
        verdict="pass",
        rationale="every intermediate matrix matches the loop-based reference",
    )


def test_same_params_cross_batch_determinism(run_log):
    x = SIGNALS["white_noise_seed7"]
    a = compute_pipeline(x, CFG)
    b = compute_pipeline(list(x), MFCCConfig())  # fresh config, list input
    for name in ("mfcc", "delta", "delta2"):
        assert np.array_equal(getattr(a, name), getattr(b, name)), name
    run_log("cross_batch_determinism", input_id=signal_id(x), verdict="pass",
            rationale="identical params+input must give bitwise-identical features")


def test_sine_energy_concentrates_near_440hz(run_log):
    """Physical sanity: a 440 Hz tone's mel energy peaks in the band
    containing 440 Hz — an assertion the reference cannot self-fulfil."""
    got = compute_pipeline(SIGNALS["sine_440"], CFG)
    band = int(np.argmax(got.mel_energies.mean(axis=0)))
    mel_centres_hz = [
        ref.mel_to_hz(ref.hz_to_mel(20.0) + i * (ref.hz_to_mel(8000.0) - ref.hz_to_mel(20.0)) / 27)
        for i in range(28)
    ]
    lo, hi = mel_centres_hz[band], mel_centres_hz[band + 2]
    assert lo <= 440.0 <= hi, f"peak band [{lo:.1f}, {hi:.1f}] Hz misses 440 Hz"
    run_log("sine_band_check", peak_band=band, band_hz=[lo, hi], verdict="pass",
            rationale="440Hz tone must peak in the mel band covering 440Hz")
