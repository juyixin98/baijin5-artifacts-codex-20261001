"""Streaming state tests: chunked processing must equal the batch result."""
import numpy as np
import pytest

from fixtures.synth import get_fixture
from wsola_backend.stream import WsolaStream
from wsola_backend.wsola import make_params, wsola_stretch


def run_stream(x, sr, ts, chunk_sizes):
    stream = WsolaStream(sr, ts)
    emitted = []
    pos = 0
    i = 0
    while pos < x.shape[0]:
        size = chunk_sizes[i % len(chunk_sizes)]
        piece = x[pos : pos + size]
        emitted.append(stream.push(piece))
        pos += piece.shape[0]
        i += 1
    emitted.append(stream.finish())
    return stream, np.concatenate([e for e in emitted if e.size])


@pytest.mark.parametrize("ts", [0.7, 1.0, 1.3, 2.0])
@pytest.mark.parametrize("chunk_sizes", [(1,), (1000,), (7777, 13), (16_000,)])
def test_stream_matches_batch(ts, chunk_sizes):
    sr, x = get_fixture("tone_440hz")
    batch = wsola_stretch(x, make_params(sr, ts))
    stream, streamed = run_stream(x, sr, ts, chunk_sizes)
    assert streamed.shape[0] == batch.target_length
    np.testing.assert_allclose(streamed, batch.output, atol=1e-9)
    assert [f.match_pos for f in stream.frames] == [f.match_pos for f in batch.frames]


def test_stream_result_assembly():
    sr, x = get_fixture("noise_seeded")
    batch = wsola_stretch(x, make_params(sr, 1.5))
    stream, _ = run_stream(x, sr, 1.5, (4096,))
    result = stream.result()
    np.testing.assert_allclose(result.output, batch.output, atol=1e-9)
    assert result.target_length == batch.target_length


def test_push_after_finish_rejected():
    stream = WsolaStream(16_000, 1.0)
    stream.push(np.zeros(4096))
    stream.finish()
    with pytest.raises(RuntimeError):
        stream.push(np.zeros(16))
    with pytest.raises(RuntimeError):
        stream.finish()
