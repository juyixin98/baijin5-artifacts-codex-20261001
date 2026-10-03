"""Streaming-state tests: latency, flush, block-size independence."""

from __future__ import annotations

import numpy as np
import pytest

from limiter import LimiterConfig, LimiterStream, limit_offline


def run_stream(cfg, pcm, block_size):
    stream = LimiterStream(cfg, num_channels=pcm.shape[1])
    outs, gains = [], []
    for start in range(0, pcm.shape[0], block_size):
        o, g = stream.process(pcm[start : start + block_size])
        outs.append(o)
        gains.append(g)
    o, g = stream.flush()
    outs.append(o)
    gains.append(g)
    return np.vstack(outs), np.concatenate(gains), stream


class TestLatency:
    def test_impulse_delayed_by_exactly_lookahead(self, default_config, short_impulse):
        pcm, meta = short_impulse
        out, gain, stream = run_stream(default_config, pcm, 512)
        k = meta["impulse_index"]
        L = default_config.lookahead_samples
        assert stream.latency_samples == L
        # uncompensated: the impulse lands L samples later
        assert np.argmax(np.abs(out[:, 0])) == k + L
        # nothing emitted before the impulse could have arrived
        assert np.all(out[: k + L] == 0.0)

    def test_delay_compensation_realigns(self, default_config, short_impulse):
        pcm, meta = short_impulse
        result = limit_offline(default_config, pcm)
        assert result.output.shape == pcm.shape
        assert np.argmax(np.abs(result.output[:, 0])) == meta["impulse_index"]

    def test_total_output_accounts_for_latency(self, default_config, sustained_peaks):
        pcm, _ = sustained_peaks
        out, gain, stream = run_stream(default_config, pcm, 333)
        L = default_config.lookahead_samples
        # raw stream: N real samples + L leading delay zeros
        assert out.shape[0] == pcm.shape[0] + L
        assert gain.shape[0] == pcm.shape[0] + L
        assert np.all(out[:L] == 0.0)
        assert stream.stats["total_out"] == stream.stats["total_in"] + L


class TestFlush:
    def test_tail_samples_not_dropped(self, default_config):
        # impulse 3 samples before the end must survive the flush
        pcm = np.zeros((1000, 2))
        pcm[-3, :] = 0.9
        out, _, _ = run_stream(default_config, pcm, 256)
        L = default_config.lookahead_samples
        assert np.argmax(np.abs(out[:, 0])) == 1000 - 3 + L
        assert np.max(np.abs(out)) > 0.0

    def test_double_flush_and_process_after_flush_rejected(self, default_config):
        stream = LimiterStream(default_config, num_channels=2)
        stream.process(np.zeros((100, 2)))
        stream.flush()
        with pytest.raises(RuntimeError, match="once"):
            stream.flush()
        with pytest.raises(RuntimeError, match="flushed"):
            stream.process(np.zeros((10, 2)))


class TestBlockSizeIndependence:
    @pytest.mark.parametrize("block_size", [1, 7, 256, 512, 1024, 4096])
    def test_output_identical_across_chunking(
        self, default_config, block_boundary_burst, block_size
    ):
        pcm, _ = block_boundary_burst
        ref_out, ref_gain, _ = run_stream(default_config, pcm, 4096)
        out, gain, _ = run_stream(default_config, pcm, block_size)
        np.testing.assert_array_equal(out, ref_out)
        np.testing.assert_array_equal(gain, ref_gain)


class TestInputValidation:
    def test_wrong_channel_count_rejected(self, default_config):
        stream = LimiterStream(default_config, num_channels=2)
        with pytest.raises(ValueError, match="shape"):
            stream.process(np.zeros((10, 3)))

    def test_non_finite_rejected(self, default_config):
        stream = LimiterStream(default_config, num_channels=2)
        block = np.zeros((10, 2))
        block[5, 0] = np.nan
        with pytest.raises(ValueError, match="non-finite"):
            stream.process(block)

    def test_mono_stream(self, default_config):
        stream = LimiterStream(default_config, num_channels=1)
        out, gain = stream.process(np.zeros(100))
        out2, _ = stream.flush()
        assert np.vstack([out, out2]).shape == (
            100 + default_config.lookahead_samples,
            1,
        )
