"""Sustained-peak probes: steady-state gain, no mid-sustain release, and
evidence the limiter applies a smooth envelope rather than per-sample
hard clipping."""

import numpy as np
import pytest

from limiter import fixtures
from limiter.stream import process_offline


def test_sustained_peak_steady_state(cfg, tlog):
    fx = fixtures.sustained_sine()
    pcm = fx.pcm()
    amp = fx.meta["amp"]
    res = process_offline(pcm, cfg)

    expected_g = cfg.threshold / amp  # 5/9
    n = len(pcm)
    L = cfg.lookahead_samples
    # NOTE: the final L frames are the flush tail — the lookahead window
    # runs off the end of the signal there and the gain correctly releases.
    # Steady state is therefore asserted away from the tail.
    mid = n // 2
    steady_region = slice(2000, n - L)
    steady = slice(n - L - 5000, n - L)
    err = float(np.max(np.abs(res.output[steady] - pcm[steady] * expected_g)))

    tlog(
        "sustained_steady_state",
        fixture_id=fx.id,
        fixture_sha256=fx.sha256(),
        expected_steady_gain=expected_g,
        measured_steady_gain=float(res.gain[mid]),
        max_gain_steady_region=float(np.max(res.gain[steady_region])),
        steady_state_envelope_error=err,
        hard_clip_error_would_be=amp - cfg.threshold,
        output_peak=res.output_peak,
        promised_ceiling=res.promised_ceiling,
    )

    # Steady-state gain converges to threshold/amp.
    assert res.gain[mid] == pytest.approx(expected_g, abs=1e-9)
    # No recovery while the signal stays hot.
    assert float(np.max(res.gain[steady_region])) <= expected_g + 1e-6
    # Ceiling promise holds for the whole sustained tone.
    assert res.output_peak <= res.promised_ceiling + 1e-12
    # NOT hard clipping: a per-sample clipper would flatten crests and show
    # errors up to amp - threshold (~0.4) in this check.
    assert err < 1e-6
