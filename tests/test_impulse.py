"""Short-impulse probes: latency, attack, ceiling promise, gain recovery,
flush tail integrity, and full-trajectory agreement with the independent
reference implementation.

Metrics and thresholds are logged BEFORE the assertions, so a failing
run still leaves the full judgment basis in results/run-*.jsonl.
"""

import math

import numpy as np
import pytest

from limiter import fixtures
from limiter.stream import StreamingLimiter, process_offline

from .reference import ref_ceiling, ref_coeffs, ref_limit, ref_recovery_curve


def test_impulse_latency_and_ceiling(cfg, tlog):
    fx = fixtures.impulse()
    pcm = fx.pcm()
    k = fx.meta["index"]
    amp = fx.meta["amplitude"]
    L = cfg.lookahead_samples

    lim = StreamingLimiter(cfg)
    # Feed exactly up to input frame k+L-1: the impulse has entered the
    # lookahead buffer but must NOT have been emitted yet.
    head = lim.process(pcm[: k + L])
    rest = lim.process(pcm[k + L :])
    tail = lim.flush()
    out = np.vstack([head.pcm, rest.pcm, tail.pcm])
    gain = np.concatenate([head.gain, rest.gain, tail.gain])

    a, _ = ref_coeffs(cfg.sample_rate, cfg.attack_ms, cfg.release_ms)
    promised = ref_ceiling(cfg.threshold, amp, a, L)
    out_peak = float(np.max(np.abs(out)))
    gain_min = float(gain.min())

    tlog(
        "impulse_latency_ceiling",
        fixture_id=fx.id,
        fixture_sha256=fx.sha256(),
        config=cfg.to_dict(),
        impulse_index=k,
        emitted_before_deadline=head.pcm.shape[0],
        head_max_abs=float(np.max(np.abs(head.pcm))) if head.pcm.size else 0.0,
        total_emitted=out.shape[0],
        tail_frames=tail.pcm.shape[0],
        argmax_output=int(np.argmax(np.abs(out[:, 0]))),
        out_peak=out_peak,
        promised_ceiling=promised,
        gain_min=gain_min,
        expected_gain_min=cfg.threshold / amp,
    )

    # Latency: nothing emitted before the lookahead deadline; what was
    # emitted carries no trace of the impulse.
    assert head.pcm.shape[0] == k
    assert np.max(np.abs(head.pcm)) == 0.0, "impulse leaked out before L frames of lookahead"
    # Flush integrity: total emitted == total input, nothing dropped.
    assert out.shape[0] == len(pcm)
    assert tail.pcm.shape[0] == L
    # Latency-compensated alignment: impulse lands at its input index.
    assert int(np.argmax(np.abs(out[:, 0]))) == k
    # Ceiling promise (independent closed form).
    assert out_peak <= promised + 1e-12
    # Gain floor: the limiter must actually attenuate to threshold/amp.
    assert gain_min == pytest.approx(cfg.threshold / amp, rel=1e-3)


def test_impulse_gain_recovery_is_gradual_and_matches_closed_form(cfg, tlog):
    # Long fixture: recovery to 0.99 with a 50 ms release takes ~10.4k
    # frames after the impulse, so the default 4096-frame fixture is too
    # short to observe full recovery.
    fx = fixtures.impulse(n=16384, index=1000)
    pcm = fx.pcm()
    k = fx.meta["index"]
    res = process_offline(pcm, cfg)

    g_start = float(res.gain[k])
    _, rel = ref_coeffs(cfg.sample_rate, cfg.attack_ms, cfg.release_ms)
    steps = 200
    expected = ref_recovery_curve(g_start, rel, steps)
    recovery_err = float(np.max(np.abs(res.gain[k : k + steps] - expected)))
    m = int(math.ceil(math.log(0.01 / (1.0 - g_start)) / math.log(rel)))

    tlog(
        "impulse_recovery",
        fixture_sha256=fx.sha256(),
        g_at_peak=g_start,
        g_one_frame_later=float(res.gain[k + 1]),
        release_coeff=rel,
        closed_form_max_err=recovery_err,
        frames_to_99pct=m,
        gain_at_recovery=float(res.gain[k + m]),
    )

    # Not a hard gate: one frame after the peak the gain is still low.
    assert res.gain[k + 1] < 0.5
    # Recovery follows the closed-form one-pole release curve.
    np.testing.assert_allclose(res.gain[k : k + steps], expected, rtol=1e-9, atol=1e-12)
    # Recovery completes: >= 0.99 within the analytically derived frame count.
    assert res.gain[k + m] >= 0.99


def test_flush_does_not_drop_tail_impulse(cfg, tlog):
    n = 3000
    pcm = np.zeros((n, cfg.channels))
    pcm[-1, :] = 2.0  # impulse in the very last input frame

    lim = StreamingLimiter(cfg)
    head = lim.process(pcm)
    tail = lim.flush()
    out = np.vstack([head.pcm, tail.pcm])

    tlog(
        "flush_tail",
        n=n,
        head_frames=head.pcm.shape[0],
        tail_frames=tail.pcm.shape[0],
        total_emitted=out.shape[0],
        last_output=float(out[-1, 0]),
        promised_ceiling=cfg.promised_ceiling(2.0),
    )

    assert head.pcm.shape[0] == n - cfg.lookahead_samples
    assert out.shape[0] == n, "flush dropped tail samples"
    # The tail impulse must appear, attenuated under the ceiling promise.
    assert out[-1, 0] > 0.4
    assert out[-1, 0] <= cfg.promised_ceiling(2.0) + 1e-12


def test_full_trajectory_matches_independent_reference(cfg, tlog):
    fx = fixtures.impulse()
    pcm = fx.pcm()
    res = process_offline(pcm, cfg)
    ref_out, ref_gain = ref_limit(
        pcm,
        threshold=cfg.threshold,
        sample_rate=cfg.sample_rate,
        attack_ms=cfg.attack_ms,
        release_ms=cfg.release_ms,
        lookahead_ms=cfg.lookahead_ms,
    )
    tlog(
        "reference_crosscheck",
        fixture_sha256=fx.sha256(),
        frames=int(len(pcm)),
        max_abs_diff=float(np.max(np.abs(res.output - ref_out))),
    )
    assert np.array_equal(res.output, ref_out)
    assert np.array_equal(res.gain, ref_gain)
