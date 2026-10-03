"""Streaming state tests: chunked processing must equal the offline core."""
from __future__ import annotations

import numpy as np
import pytest

from wsola_backend import fixtures
from wsola_backend.stream import StreamFinalizedError, WsolaStream
from wsola_backend.wsola import InputTooShortError, wsola_stretch


def run_streamed(x: np.ndarray, rate: float, chunk_size: int):
    stream = WsolaStream(rate=rate)
    chunks = []
    for start in range(0, x.shape[0], chunk_size):
        chunks.append(stream.push(x[start : start + chunk_size]))
    result = stream.finalize()
    chunks.append(result.samples)
    return np.concatenate(chunks), result, stream


@pytest.mark.parametrize("rate", [0.75, 1.3])
@pytest.mark.parametrize("chunk_size", [1, 13, 512, 1000, 5000])
def test_streaming_matches_offline_bit_for_bit(rate, chunk_size):
    x = fixtures.noise(5000, seed=7)
    offline = wsola_stretch(x, rate=rate)
    y, streamed_result, stream = run_streamed(x, rate, chunk_size)
    assert np.array_equal(y, offline.samples)  # exact, not approximate
    assert [s.delta for s in streamed_result.segments] == [
        s.delta for s in offline.segments
    ]
    assert streamed_result.frames_placed == offline.frames_placed
    assert streamed_result.end_rule == offline.end_rule
    assert streamed_result.end_compensation_samples == (
        offline.end_compensation_samples
    )
    assert streamed_result.max_position_drift == offline.max_position_drift


def test_push_after_finalize_rejected():
    stream = WsolaStream(rate=1.0)
    stream.push(fixtures.noise(2048, seed=8))
    stream.finalize()
    with pytest.raises(StreamFinalizedError):
        stream.push(np.zeros(16))


def test_finalize_with_too_little_input_rejected():
    stream = WsolaStream(rate=1.0)
    stream.push(np.zeros(100))
    with pytest.raises(InputTooShortError):
        stream.finalize()


def test_incremental_segments_are_prefix_of_offline():
    # Segments decided mid-stream must never be revised later.
    x = fixtures.noise(6000, seed=9)
    offline = wsola_stretch(x, rate=1.2)
    stream = WsolaStream(rate=1.2)
    stream.push(x[:3000])
    decided_early = [s.delta for s in stream.segments]
    assert decided_early, "expected some frames decided after 3000 samples"
    stream.push(x[3000:])
    stream.finalize()
    final = [s.delta for s in stream.segments]
    assert final[: len(decided_early)] == decided_early
    assert final == [s.delta for s in offline.segments]
