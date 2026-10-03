"""Stereo-imbalance probes: fully linked gain (one scalar gain for all
channels, driven by the loudest channel, stereo ratio preserved)."""

import numpy as np
import pytest

from limiter import fixtures
from limiter.stream import process_offline


def test_linked_gain_driven_by_loud_channel(cfg, tlog):
    fx = fixtures.stereo_imbalanced()
    pcm = fx.pcm()
    res = process_offline(pcm, cfg)

    left_amp = fx.meta["left_amp"]
    right_amp = fx.meta["right_amp"]
    expected_g = cfg.threshold / left_amp
    mid = len(pcm) // 2
    # Steady region, excluding the flush tail (the lookahead window runs
    # off the end of the signal there and the gain correctly releases).
    steady = slice(5000, len(pcm) - cfg.lookahead_samples)
    right_out_peak = float(np.max(np.abs(res.output[steady, 1])))
    mask = np.abs(pcm[steady, 1]) > 0.1
    ratio = res.output[steady, 0][mask] / res.output[steady, 1][mask]
    ratio_err = float(np.max(np.abs(ratio - left_amp / right_amp)))

    tlog(
        "stereo_link",
        fixture_id=fx.id,
        fixture_sha256=fx.sha256(),
        expected_steady_gain=expected_g,
        measured_steady_gain=float(res.gain[mid]),
        right_out_peak=right_out_peak,
        expected_right_out_peak=right_amp * expected_g,
        stereo_ratio_err=ratio_err,
        output_peak=res.output_peak,
        promised_ceiling=res.promised_ceiling,
    )

    # Ceiling applies to the loud channel.
    assert res.output_peak <= res.promised_ceiling + 1e-12
    # Steady-state gain is set by the LEFT channel: threshold / left_amp.
    assert res.gain[mid] == pytest.approx(expected_g, abs=1e-9)
    # Linking: the quiet right channel is scaled by the SAME gain trajectory.
    assert np.array_equal(res.output[:, 1], pcm[:, 1] * res.gain)
    assert np.array_equal(res.output[:, 0], pcm[:, 0] * res.gain)
    # The quiet channel is attenuated far below its own peak (0.2 -> ~0.05),
    # proving it did not get an independent, gentler gain.
    assert right_out_peak == pytest.approx(right_amp * expected_g, rel=1e-3)
    # Stereo ratio preserved in steady state (same scalar gain on both).
    np.testing.assert_allclose(ratio, left_amp / right_amp, rtol=1e-9)
