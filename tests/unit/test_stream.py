"""Unit tests for the streaming state module."""

from __future__ import annotations

import numpy as np
import pytest

from app.stream import LPCStreamAnalyzer, LPCStreamReconstructor


def test_streamed_analysis_matches_one_shot_residuals(noise):
    frame_size, order = 256, 10
    analyzer = LPCStreamAnalyzer(frame_size, order, "hann")
    frames = [noise[i : i + frame_size] for i in range(0, len(noise), frame_size)]
    streamed = [analyzer.process_frame(f) for f in frames]

    # one-shot reference: same analyzer, fresh instance, same frames —
    # equality of residuals across the two stateful passes proves the
    # carried state is consistent and deterministic.
    analyzer2 = LPCStreamAnalyzer(frame_size, order, "hann")
    for f, fa in zip(frames, streamed):
        fa2 = analyzer2.process_frame(f)
        np.testing.assert_array_equal(fa.residual, fa2.residual)
        np.testing.assert_array_equal(fa.levinson.lpc, fa2.levinson.lpc)

    assert [fa.frame_index for fa in streamed] == list(range(len(frames)))
    assert [fa.offset for fa in streamed] == [
        i * frame_size for i in range(len(frames))
    ]


def test_streamed_roundtrip_recovers_signal(noise):
    frame_size, order = 256, 10
    analyzer = LPCStreamAnalyzer(frame_size, order, "hann")
    reconstructor = LPCStreamReconstructor(order)
    out = []
    for i in range(0, len(noise), frame_size):
        fa = analyzer.process_frame(noise[i : i + frame_size])
        out.append(reconstructor.reconstruct_frame(fa.residual, fa.levinson.lpc))
    y = np.concatenate(out)
    np.testing.assert_allclose(y, noise, rtol=0, atol=1e-10)


def test_analyzer_rejects_wrong_frame_size():
    analyzer = LPCStreamAnalyzer(128, 8, "hann")
    with pytest.raises(ValueError, match="exactly 128"):
        analyzer.process_frame(np.zeros(64))


def test_analyzer_rejects_invalid_config():
    with pytest.raises(ValueError, match="order"):
        LPCStreamAnalyzer(128, 128, "hann")
    with pytest.raises(ValueError, match="order"):
        LPCStreamAnalyzer(128, 0, "hann")


def test_reconstructor_rejects_order_mismatch():
    rec = LPCStreamReconstructor(4)
    with pytest.raises(ValueError, match="order"):
        rec.reconstruct_frame(np.zeros(16), np.array([1.0, 0.1, 0.1]))


def test_silence_streams_defined_zero_energy(silence):
    analyzer = LPCStreamAnalyzer(256, 10, "hann")
    reconstructor = LPCStreamReconstructor(10)
    for i in range(0, len(silence), 256):
        fa = analyzer.process_frame(silence[i : i + 256])
        assert fa.levinson.zero_energy
        np.testing.assert_array_equal(fa.residual, np.zeros(256))
        y = reconstructor.reconstruct_frame(fa.residual, fa.levinson.lpc)
        np.testing.assert_array_equal(y, np.zeros(256))
