"""Per-segment match offset tests.

Reference values here are derived BY HAND from the fixed rules (scan order
0,-1,+1,..., strict-greater wins, degenerate -> 0.0) or produced by an
independent re-implementation in this file — never by the core under test.
"""
from __future__ import annotations

import numpy as np
import pytest

from wsola_backend import fixtures
from wsola_backend.wsola import delta_scan_order, wsola_stretch


# ---------------------------------------------------------------------------
# Independent reference implementation (separate code path from the core).
# ---------------------------------------------------------------------------
def reference_deltas(x: np.ndarray, rate: float, cfg) -> list[int]:
    """Brute-force WSOLA deltas, written independently of wsola.py."""
    Hs, L, D = cfg.synthesis_hop, cfg.window_length, cfg.search_radius
    n = len(x)
    win = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(L) / L)
    weight = win[Hs:]  # both match sides carry the overlap-window weight
    target = int(round(n / rate))
    frames = max(1, int(np.ceil((target - L) / Hs)) + 1)
    y = np.zeros((frames - 1) * Hs + L)
    deltas: list[int] = []
    for k in range(frames):
        nominal = int(round(k * rate * Hs))
        if k == 0:
            delta = 0
        else:
            lo, hi = max(-D, -nominal), min(D, n - L - nominal)
            if lo > hi:
                # documented tail rule: search the last D placeable starts
                hi = n - L - nominal
                lo = max(hi - D, -nominal)
            overlap = y[k * Hs : k * Hs + Hs]
            best_score, best_delta = -np.inf, None
            radius = D + max(0, -hi)  # scan far enough to reach lo
            order = [0] + [d for m in range(1, radius + 1) for d in (-m, m)]
            for d in order:
                if d < lo or d > hi:
                    continue
                cand = x[nominal + d : nominal + d + Hs] * weight
                denom = np.sqrt((overlap @ overlap) * (cand @ cand))
                score = 0.0 if denom < 1e-12 else float(overlap @ cand / denom)
                if score > best_score:
                    best_score, best_delta = score, d
            delta = best_delta
        deltas.append(delta)
        a = nominal + delta
        y[k * Hs : k * Hs + L] += win * x[a : a + L]
    return deltas


# ---------------------------------------------------------------------------
# Hand-computed cases (tiny_cfg: L=8, Hs=4, D=2; periodic Hann win[6] = 0.5).
# ---------------------------------------------------------------------------
def test_misaligned_impulse_train_picks_hand_computed_delta(tiny_cfg):
    # Impulses at positions == 2 (mod 4); rate 1.25 -> analysis hop 5.
    # Frame 0 covers x[0:8]: impulse at abs 6 -> overlap impulse at rel 2.
    # Frame 1 nominal = 5; correlation is nonzero only when x[7+d] is an
    # impulse, i.e. d == -1 within [-2, 2]. Expected: delta=-1, corr=1.0.
    x = fixtures.impulse_train(period_samples=4, n_samples=64, offset=2)
    result = wsola_stretch(x, rate=1.25, cfg=tiny_cfg)
    assert result.segments[1].nominal_position == 5
    assert result.segments[1].delta == -1
    assert result.segments[1].correlation == pytest.approx(1.0)


def test_symmetric_tie_resolves_to_first_in_scan_order(tiny_cfg):
    # rate 2.0 -> analysis hop 8. Impulses at 6 (frame-0 overlap, rel 2),
    # 9 and 11 (equidistant around frame-1 nominal 8 + rel 2 = 10).
    # d=-1 and d=+1 both score 1.0; fixed scan order 0,-1,+1,... -> -1 wins.
    x = np.zeros(32)
    x[[6, 9, 11]] = 1.0
    result = wsola_stretch(x, rate=2.0, cfg=tiny_cfg)
    assert result.segments[1].nominal_position == 8
    assert result.segments[1].delta == -1
    assert result.segments[1].correlation == pytest.approx(1.0)


def test_silence_is_degenerate_and_picks_delta_zero(small_cfg):
    x = fixtures.silence(512)
    result = wsola_stretch(x, rate=1.3, cfg=small_cfg)
    assert result.segments[0].delta == 0
    assert result.segments[0].correlation is None
    later = result.segments[1:]
    assert later, "expected multiple segments for this fixture"
    n, L, D = 512, small_cfg.window_length, small_cfg.search_radius
    for seg in later:
        assert seg.degenerate is True
        assert seg.correlation == 0.0
        # Degenerate tie-break: first candidate in scan order inside the
        # clamped range [lo, hi] == clamp(0, lo, hi). Usually 0; near the
        # end of input the clamp forces the boundary value instead.
        lo = max(-D, -seg.nominal_position)
        hi = min(D, n - L - seg.nominal_position)
        assert seg.delta == min(max(0, lo), hi)
    # Hand-computed tail case: segment 11 has nominal 458, hi = 512-64-458
    # = -10, so the forced degenerate choice is exactly -10.
    assert result.segments[11].nominal_position == 458
    assert result.segments[11].delta == -10


def test_periodic_signal_ties_prefer_smallest_scan_order_magnitude(tiny_cfg):
    # rate 1.0 on a period-4 impulse train: d=0 aligns exactly, so despite
    # d=+/-4 also aligning (outside D=2 here), delta must be exactly 0.
    x = fixtures.impulse_train(period_samples=4, n_samples=64, offset=2)
    result = wsola_stretch(x, rate=1.0, cfg=tiny_cfg)
    for seg in result.segments:
        assert seg.delta == 0


def test_scan_order_is_fixed_and_zero_first():
    assert list(delta_scan_order(3)) == [0, -1, 1, -2, 2, -3, 3]


# ---------------------------------------------------------------------------
# Cross-check against the independent reference implementation.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rate", [0.75, 1.0, 1.3, 2.0])
def test_deltas_match_independent_reference_on_noise(small_cfg, rate):
    x = fixtures.noise(700, seed=42)
    result = wsola_stretch(x, rate=rate, cfg=small_cfg)
    expected = reference_deltas(x, rate, small_cfg)
    actual = [s.delta for s in result.segments]
    assert actual == expected


@pytest.mark.parametrize("rate", [0.8, 1.5])
def test_deltas_match_independent_reference_on_tone(small_cfg, rate):
    x = fixtures.tone(440.0, duration_s=0.2, sample_rate=8000)
    result = wsola_stretch(x, rate=rate, cfg=small_cfg)
    expected = reference_deltas(x, rate, small_cfg)
    actual = [s.delta for s in result.segments]
    assert actual == expected
