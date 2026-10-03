"""Peak-ceiling, channel-linking and gain-recovery tests on fixtures.

Each test asserts concrete numeric expectations (ceiling, steady-state
gain, recovery time constant, channel ratio) and names the failure
category in its assertion messages.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.signal import resample_poly

from limiter import LimiterConfig, limit_offline

# tolerance for float64 round-trip in the ceiling guarantee
CEILING_RTOL = 1e-9


class TestSamplePeakCeiling:
    def test_sustained_peaks_never_exceed_threshold(
        self, default_config, sustained_peaks
    ):
        pcm, meta = sustained_peaks
        result = limit_offline(default_config, pcm)
        peak = np.max(np.abs(result.output))
        T = default_config.threshold_linear
        assert peak <= T * (1 + CEILING_RTOL), (
            f"CEILING VIOLATION: sustained output peak {peak:.9f} > threshold {T:.9f}"
        )

    def test_sustained_steady_state_gain(self, default_config, sustained_peaks):
        pcm, meta = sustained_peaks
        result = limit_offline(default_config, pcm)
        expected = default_config.threshold_linear / meta["amplitude"]
        steady = result.gain[1000:]  # well past attack/release settling
        # the gain dips to exactly threshold/amplitude at every sine crest
        assert abs(steady.min() - expected) < 0.01, (
            f"STEADY-STATE GAIN WRONG: expected ~{expected:.4f} at crests, "
            f"got min {steady.min():.4f}"
        )
        # and never fully recovers between crests (continuous limiting)
        assert steady.max() < 0.99, "gain fully recovered: limiter not tracking"
        # output crests sit at the threshold
        T = default_config.threshold_linear
        crest_peak = np.max(np.abs(result.output[1000:]))
        assert crest_peak == pytest.approx(T, rel=1e-3)

    def test_block_boundary_burst_ceiling(self, default_config, block_boundary_burst):
        pcm, meta = block_boundary_burst
        result = limit_offline(
            default_config, pcm, block_size=meta["block_size"]
        )
        T = default_config.threshold_linear
        lo, hi = meta["burst_range"]
        burst_peak = np.max(np.abs(result.output[lo - 4 : hi + 4]))
        assert burst_peak <= T * (1 + CEILING_RTOL), (
            f"CEILING VIOLATION at block boundary: burst peak {burst_peak:.9f} "
            f"> threshold {T:.9f}"
        )
        assert result.stats["sample_peak_ceiling_ok"]


class TestStereoLinking:
    def test_imbalanced_channels_share_one_gain(
        self, default_config, stereo_imbalance
    ):
        pcm, meta = stereo_imbalance
        result = limit_offline(default_config, pcm)
        T = default_config.threshold_linear
        out = result.output
        # ceiling on the loud channel
        assert np.max(np.abs(out[:, 0])) <= T * (1 + CEILING_RTOL), (
            "CEILING VIOLATION on loud channel"
        )
        # linked gain preserves the inter-channel ratio exactly
        mask = np.abs(out[:, 0]) > 1e-6
        ratio = out[mask, 1] / out[mask, 0]
        np.testing.assert_allclose(
            ratio,
            meta["channel_ratio"],
            rtol=1e-9,
            err_msg="CHANNEL LINKING BROKEN: inter-channel ratio drifted",
        )
        # quiet channel stays proportionally quiet (no independent make-up)
        assert np.max(np.abs(out[:, 1])) <= T * meta["channel_ratio"] * 1.05


class TestGainRecovery:
    def test_release_time_constant(self, default_config, gain_recovery):
        pcm, meta = gain_recovery
        result = limit_offline(default_config, pcm)
        gain = result.gain
        k = meta["impulse_index"]
        T = default_config.threshold_linear
        g_min_expected = T / meta["impulse_amplitude"]

        # gain bottoms out exactly at the impulse sample
        k_min = k + int(np.argmin(gain[k : k + 50]))
        assert gain[k_min] == pytest.approx(g_min_expected, rel=1e-6), (
            f"GAIN DEPTH WRONG: expected {g_min_expected:.6f}, got {gain[k_min]:.6f}"
        )

        # multiplicative release: g reaches 0.99 after
        # tau_rel * fs * ln(0.99 / g_min) samples (analytic expectation)
        fs = default_config.sample_rate
        n_expected = (
            default_config.release_ms / 1000.0 * fs * math.log(0.99 / gain[k_min])
        )
        after = gain[k_min:]
        recovered = np.nonzero(after >= 0.99)[0]
        assert recovered.size > 0, "RECOVERY FAILURE: gain never returned to 0.99"
        n_actual = int(recovered[0])
        assert abs(n_actual - n_expected) <= 2, (
            f"RECOVERY RATE WRONG: expected ~{n_expected:.0f} samples to 0.99, "
            f"got {n_actual}"
        )

    def test_attack_anticipation_within_lookahead(self, default_config, short_impulse):
        pcm, meta = short_impulse
        result = limit_offline(default_config, pcm)
        gain = result.gain
        k = meta["impulse_index"]
        L = default_config.lookahead_samples
        # gain must already be falling before the impulse arrives...
        assert gain[k - 1] < 1.0, "NO LOOKAHEAD: gain did not anticipate the peak"
        # ...but not earlier than the lookahead window allows
        assert gain[k - L - 1] == pytest.approx(1.0), (
            "gain moved earlier than the lookahead window"
        )


class TestTruePeakMode:
    def test_true_peak_ceiling_on_intersample_content(
        self, default_config, true_peak_rich
    ):
        pcm, meta = true_peak_rich
        cfg = LimiterConfig.from_dict(
            {**default_config.to_dict(), "true_peak": True, "lookahead_ms": 8.0}
        )
        result = limit_offline(cfg, pcm)
        # measure the reconstructed peak of the output the same way a
        # meter would: 4x polyphase oversampling
        up = resample_poly(result.output, 4, 1, axis=0)
        true_peak = np.max(np.abs(up[64:-64]))  # skip filter edge transients
        T = cfg.threshold_linear
        # documented tolerance: polyphase ripple + base-rate gain quantization
        tol = 10.0 ** (0.1 / 20.0)  # +0.1 dB
        assert true_peak <= T * tol, (
            f"TRUE-PEAK CEILING VIOLATION: reconstructed peak {true_peak:.6f} "
            f"> threshold {T:.6f} (+0.1 dB tol)"
        )
        # and the limiter actually engaged (sample peak alone is under T)
        assert np.max(np.abs(pcm)) < T, "fixture should not trigger sample-peak mode"
        assert result.gain.min() < 1.0, "true-peak limiter did not engage"

    def test_sample_peak_mode_ignores_intersample_overshoot(
        self, default_config, true_peak_rich
    ):
        """Documented semantics: sample-peak mode constrains samples only."""
        pcm, _ = true_peak_rich
        result = limit_offline(default_config, pcm)
        assert result.gain.min() == pytest.approx(1.0), (
            "sample-peak mode must not react to sub-threshold sample peaks"
        )
