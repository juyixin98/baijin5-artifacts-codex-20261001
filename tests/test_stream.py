"""Tests for the streaming state machines (StreamStft / StreamOla)."""

import numpy as np
import pytest

from app.errors import ShapeMismatchError, StreamStateError
from app.stft_core import StftParams, istft, stft, window_buffer
from app.stream import StreamOla, StreamStft

PARAMS = StftParams(n_fft=64, win_length=64, hop_length=16)


def test_stream_matches_batch_frames():
    """Chunked streaming must produce exactly the frames a batch framing
    (without centre padding) would: frame k = rfft(x[k*hop : k*hop+n_fft] * w).
    """
    rng = np.random.default_rng(9)
    x = rng.standard_normal(500)
    win = window_buffer(PARAMS)

    stream = StreamStft(PARAMS)
    chunks = [x[:100], x[100:233], x[233:]]
    got = [stream.push(c) for c in chunks]
    got.append(stream.flush())
    S = np.concatenate(got, axis=0)

    # Reference computed test-side, straight from the definition.
    n_frames = (len(x) - 1) // PARAMS.hop_length + 1  # starts 0..last sample
    ref = []
    for k in range(n_frames):
        frame = np.zeros(PARAMS.n_fft)
        seg = x[k * PARAMS.hop_length : k * PARAMS.hop_length + PARAMS.n_fft]
        frame[: len(seg)] = seg
        ref.append(np.fft.rfft(frame * win))
    ref = np.stack(ref)

    assert S.shape == ref.shape
    np.testing.assert_allclose(S, ref, atol=1e-12)


def test_stream_state_bookkeeping():
    stream = StreamStft(PARAMS)
    assert stream.samples_received == 0
    assert stream.frames_emitted == 0
    assert stream.next_frame_start == 0

    out = stream.push(np.zeros(64))
    assert out.shape == (1, PARAMS.n_bins)
    assert stream.samples_received == 64
    assert stream.frames_emitted == 1
    assert stream.next_frame_start == 16  # frame 1 starts at sample 16

    out = stream.push(np.zeros(48))  # 112 total -> frames at 16,32,48
    assert out.shape == (3, PARAMS.n_bins)
    assert stream.frames_emitted == 4

    tail = stream.flush()
    # Last sample is 111; frames start while start <= 111: 64, 80, 96.
    assert tail.shape == (3, PARAMS.n_bins)
    assert stream.frames_emitted == 7
    assert stream.flushed


def test_stream_rejects_push_after_flush():
    stream = StreamStft(PARAMS)
    stream.push(np.zeros(64))
    stream.flush()
    with pytest.raises(StreamStateError):
        stream.push(np.zeros(16))
    with pytest.raises(StreamStateError):
        stream.flush()


def test_stream_rejects_non_1d_chunk():
    stream = StreamStft(PARAMS)
    with pytest.raises(ShapeMismatchError):
        stream.push(np.zeros((2, 8)))


def test_stream_ola_roundtrip_incremental():
    """Feed batch-STFT frames one at a time into StreamOla; the concatenated
    kept-region output must equal the batch ISTFT of the same spectrogram."""
    rng = np.random.default_rng(13)
    x = rng.standard_normal(300)
    result = stft(x, PARAMS)
    batch = istft(result.spectrogram, PARAMS, length=len(x)).samples

    ola = StreamOla(PARAMS, kept_length=len(x))
    pieces = [
        ola.push_frames(result.spectrogram[k : k + 1])
        for k in range(result.n_frames)
    ]
    pieces.append(ola.flush())
    streamed = np.concatenate(pieces)

    # Default kept_offset = pad: output is the original signal directly.
    assert streamed.shape == (len(x),)
    np.testing.assert_allclose(streamed, batch, atol=1e-12)
    np.testing.assert_allclose(streamed, x, atol=1e-9)
    assert ola.frames_received == result.n_frames


def test_stream_ola_emits_only_final_samples():
    result = stft(np.ones(200), PARAMS)

    # Default kept_offset = pad (32): nothing is emitted until finalised
    # positions pass the offset.
    ola = StreamOla(PARAMS, kept_length=200)
    assert ola.push_frames(result.spectrogram[0:1]).shape == (0,)
    out = ola.push_frames(result.spectrogram[1:3])
    # 3 frames received -> finalised up to sample 48; kept starts at 32.
    assert out.shape == (48 - PARAMS.pad,)
    assert ola.samples_emitted == 48

    # With kept_offset=0 and a rect window (denominator 1 everywhere),
    # finalisation starts immediately after the first frame.
    rect = StftParams(n_fft=64, win_length=64, hop_length=16, window="rect")
    ola_rect = StreamOla(rect, kept_offset=0)
    out0 = ola_rect.push_frames(result.spectrogram[0:1])
    assert out0.shape == (rect.hop_length,)
    assert ola_rect.samples_emitted == rect.hop_length


def test_stream_ola_rejects_wrong_bin_count():
    ola = StreamOla(PARAMS)
    with pytest.raises(ShapeMismatchError):
        ola.push_frames(np.zeros((1, PARAMS.n_bins + 1), dtype=complex))
