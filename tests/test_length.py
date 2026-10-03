"""Output length, rounding compensation and end-rule tests."""
from __future__ import annotations

import numpy as np
import pytest

from wsola_backend import fixtures
from wsola_backend.wsola import InputTooShortError, target_length_for, wsola_stretch


@pytest.mark.parametrize("n", [1024, 2000, 12345])
@pytest.mark.parametrize("rate", [0.25, 0.5, 0.75, 1.0, 1.3, 2.0, 4.0])
def test_output_length_equals_rounded_target(n, rate):
    x = fixtures.noise(n, seed=1)
    result = wsola_stretch(x, rate=rate)
    assert result.samples.shape[0] == round(n / rate)
    assert result.target_length == round(n / rate)


@pytest.mark.parametrize("rate", [0.31, 0.77, 1.13, 1.97, 3.7])
def test_position_drift_never_exceeds_half_sample(rate):
    # Per-segment rounding (nominal = round(k * Ha)) is the explicit
    # compensation: drift is bounded by 0.5 and never accumulates.
    x = fixtures.noise(8000, seed=2)
    result = wsola_stretch(x, rate=rate)
    assert result.max_position_drift <= 0.5 + 1e-9
    for seg in result.segments:
        assert abs(seg.nominal_position - seg.ideal_position) <= 0.5 + 1e-9
        assert seg.analysis_position == seg.nominal_position + seg.delta


def test_tail_pinning_hand_computed():
    # n=1024, rate=0.25 -> target 4096, 7 frames planned, Ha=128.
    # Last placeable frame start is n-L = 0. Frames 1-2 still have a
    # (collapsed) search range: forced deltas -128, -256. Frames 3-6 have
    # lo > hi -> pinned to analysis position 0 with delta = -nominal.
    x = fixtures.noise(1024, seed=3)
    result = wsola_stretch(x, rate=0.25)
    assert result.frames_planned == 7
    assert result.frames_placed == 7
    assert [s.delta for s in result.segments] == [
        0, -128, -256, -384, -512, -640, -768
    ]
    assert [s.pinned for s in result.segments] == [
        False, False, False, True, True, True, True
    ]
    assert [s.analysis_position for s in result.segments[3:]] == [0, 0, 0, 0]
    assert result.end_rule == "exact"
    assert result.end_compensation_samples == 0
    assert result.samples.shape[0] == 4096


def test_strong_slowdown_tail_is_pinned_not_zero_padded():
    # 1 s tone at 0.5x: the ideal trajectory ends at sample 16000 but the
    # last placeable start is 14976, so the tail must be pinned (audible
    # content) rather than zero-padded (48 ms of silence).
    x = fixtures.tone(440.0, duration_s=1.0)
    result = wsola_stretch(x, rate=0.5)
    assert any(s.pinned for s in result.segments)
    assert result.samples.shape[0] == 32_000
    tail = result.samples[-512:]
    assert np.max(np.abs(tail)) > 0.1  # pinned content, not zeros


def test_trim_end_rule_applies_for_speedup():
    x = fixtures.noise(4096, seed=4)
    result = wsola_stretch(x, rate=2.0)
    assert result.end_rule in ("trim", "exact")
    assert result.end_compensation_samples <= 0
    assert not any(s.pinned for s in result.segments)
    assert result.samples.shape[0] == 2048


def test_input_shorter_than_one_window_rejected():
    with pytest.raises(InputTooShortError):
        wsola_stretch(fixtures.noise(100, seed=5), rate=1.0)


def test_target_length_contract():
    assert target_length_for(16000, 2.0) == 8000
    assert target_length_for(16000, 0.5) == 32000
    assert target_length_for(1001, 3.0) == 334  # round-half-even on 333.67
