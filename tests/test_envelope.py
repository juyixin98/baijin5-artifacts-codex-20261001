"""Unit tests for the gain-envelope primitives.

Expected values are hand-computed from the documented formulas, not
produced by the implementation under test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from limiter import LimiterConfig
from limiter.envelope import (
    attack_limited_minimum,
    gain_trajectory,
    release_pass,
    required_gain,
)


class TestConfigCoefficients:
    def test_coefficients_match_closed_form(self, default_config):
        cfg = default_config
        fs = cfg.sample_rate
        assert cfg.attack_coeff == pytest.approx(math.exp(-1000.0 / (cfg.attack_ms * fs)))
        assert cfg.release_coeff == pytest.approx(math.exp(1000.0 / (cfg.release_ms * fs)))
        assert cfg.lookahead_samples == round(cfg.lookahead_ms * fs / 1000.0)
        assert cfg.threshold_linear == pytest.approx(10.0 ** (cfg.threshold_dbfs / 20.0))

    def test_attack_below_one_release_above_one(self, default_config):
        assert 0.0 < default_config.attack_coeff < 1.0
        assert default_config.release_coeff > 1.0

    def test_invalid_configs_rejected(self):
        with pytest.raises(ValueError, match="threshold_dbfs"):
            LimiterConfig(threshold_dbfs=1.0).validate()
        with pytest.raises(ValueError, match="attack_ms"):
            LimiterConfig(attack_ms=0.0).validate()
        with pytest.raises(ValueError, match="lookahead"):
            LimiterConfig(true_peak=True, lookahead_ms=0.5).validate()
        with pytest.raises(ValueError, match="unknown config keys"):
            LimiterConfig.from_dict({"not_a_key": 1})


class TestRequiredGain:
    def test_hand_computed(self):
        peaks = np.array([0.0, 0.25, 0.5, 1.0])
        r = required_gain(peaks, threshold=0.5)
        np.testing.assert_allclose(r, [1.0, 1.0, 1.0, 0.5])

    def test_never_exceeds_one(self):
        r = required_gain(np.array([1e-12, 0.1]), threshold=0.9)
        assert np.all(r <= 1.0)


class TestAttackLimitedMinimum:
    def test_hand_computed_ramp(self):
        # r=[1,1,1,0.1,1], att=0.5, L=2
        # g1[n] = min(r[n], 2*r[n+1], 4*r[n+2]); r beyond end = 1
        r = np.array([1.0, 1.0, 1.0, 0.1, 1.0])
        g1 = attack_limited_minimum(r, attack_coeff=0.5, lookahead=2)
        np.testing.assert_allclose(g1, [1.0, 0.4, 0.2, 0.1, 1.0])

    def test_structural_ceiling_and_slew(self):
        rng = np.random.default_rng(7)
        r = rng.uniform(0.05, 1.0, size=500)
        att, L = 0.9, 8
        g1 = attack_limited_minimum(r, att, L)
        # ceiling: g1 never exceeds the required gain at the same sample
        assert np.all(g1 <= r + 1e-15)
        # downward slew is attack-limited; the ONLY permitted exception is
        # the window-edge entry: a peak deeper than att**-L can reach steps
        # in exactly as the edge term r[n+L+1] * att**-L enters the window.
        r_ext = np.concatenate([r, np.ones(L + 1)])
        edge = r_ext[L + 1 :] * att ** -L  # edge term entering at each n
        for n in range(len(r) - 1):
            if g1[n + 1] < g1[n] * att - 1e-12:
                assert g1[n + 1] == pytest.approx(edge[n], rel=1e-9), (
                    f"slew violation at {n} is not a window-edge entry"
                )

    def test_smooth_required_gain_has_strict_slew(self):
        # smoothly varying r (always reachable within the window): the
        # attack slew bound holds everywhere, no exceptions
        n = np.arange(1000)
        r = 0.5 + 0.4 * np.sin(2 * np.pi * n / 300.0)
        att, L = 0.95, 16
        g1 = attack_limited_minimum(r, att, L)
        assert np.all(g1[1:] >= g1[:-1] * att - 1e-12)


class TestReleasePass:
    def test_hand_computed_recovery(self):
        g1 = np.array([0.25, 1.0, 1.0, 1.0])
        g = release_pass(g1, release_coeff=2.0)
        np.testing.assert_allclose(g, [0.25, 0.5, 1.0, 1.0])

    def test_state_carryover(self):
        g1 = np.array([1.0, 1.0])
        g = release_pass(g1, release_coeff=2.0, initial=0.3)
        np.testing.assert_allclose(g, [0.6, 1.0])


class TestGainTrajectoryInvariants:
    def test_no_hard_clip_slew_bounds(self):
        """The trajectory must be envelope-smoothed, not sample-clipped:
        gain never rises faster than release, never drops faster than
        attack except bounded window-edge entries (see attack tests)."""
        rng = np.random.default_rng(11)
        r = rng.uniform(0.02, 1.0, size=2000)
        att, rel, L = 0.95, 1.002, 16
        g = gain_trajectory(r, att, rel, L)
        assert np.all(g[1:] <= g[:-1] * rel + 1e-12), "gain rose faster than release"
        assert np.all(g <= r + 1e-15), "gain exceeded required gain (ceiling)"
        # drops faster than attack are rare, single-sample window-edge
        # entries, never a per-sample clip of the signal
        fast_drops = np.nonzero(g[1:] < g[:-1] * att - 1e-9)[0]
        assert len(fast_drops) <= len(r) // 50, (
            f"too many fast drops ({len(fast_drops)}): envelope degenerated "
            "towards sample-wise clipping"
        )

    def test_gain_reaches_required_value_at_peak(self):
        # Deep isolated dip: gain must equal r exactly at the dip sample.
        r = np.ones(64)
        r[32] = 0.3
        g = gain_trajectory(r, 0.9, 1.01, 16)
        assert g[32] == pytest.approx(0.3)
