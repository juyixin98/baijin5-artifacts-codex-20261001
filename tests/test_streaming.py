"""Streaming: batch equivalence, no future leakage, state-machine errors."""

import numpy as np
import pytest

from mfcc_backend import MFCCConfig, StreamingMFCC, StreamStateError, extract_features

# Streaming and batch run the FFT/DCT on different block shapes, so float
# reduction order differs at the ~1e-13 level.  1e-9 is far below anything a
# framing/boundary bug (off-by-one frame, wrong context) could hide under —
# those produce O(1) errors.
TOL = dict(atol=1e-9, rtol=0)


def _run_stream(x, cfg, chunk_sizes):
    stream = StreamingMFCC(cfg)
    blocks, deltas, deltas2 = [], [], []
    pos = 0
    for size in chunk_sizes:
        chunk = x[pos : pos + size]
        if len(chunk) == 0:
            continue
        pos += size
        emit = stream.accept_chunk(chunk)
        blocks.append(emit.mfcc)
        deltas.append(emit.delta)
        deltas2.append(emit.delta_delta)
    assert pos == len(x), "test chunking must cover the whole signal"
    emit = stream.finalize()
    blocks.append(emit.mfcc)
    deltas.append(emit.delta)
    deltas2.append(emit.delta_delta)
    return (np.vstack(blocks), np.vstack(deltas), np.vstack(deltas2))


def test_streaming_matches_batch_random_chunkings(white_noise, tlog):
    x, sr, input_id = white_noise
    cfg = MFCCConfig(sample_rate=sr)
    batch = extract_features(x, cfg)
    rng = np.random.default_rng(1234)
    for trial in range(5):
        sizes = []
        remaining = len(x)
        while remaining:
            size = int(rng.integers(1, 1500))
            sizes.append(min(size, remaining))
            remaining -= sizes[-1]
        mfcc, delta, delta2 = _run_stream(x, cfg, sizes)
        tlog.step("stream_vs_batch", input_id=input_id,
                  basis="concatenated stream output == batch, atol=1e-9",
                  trial=trial, n_chunks=len(sizes),
                  mfcc_max_err=float(np.max(np.abs(mfcc - batch.mfcc))),
                  delta_max_err=float(np.max(np.abs(delta - batch.delta))),
                  delta2_max_err=float(np.max(np.abs(delta2 - batch.delta_delta))))
        assert mfcc.shape == batch.mfcc.shape
        np.testing.assert_allclose(mfcc, batch.mfcc, **TOL)
        np.testing.assert_allclose(delta, batch.delta, **TOL)
        np.testing.assert_allclose(delta2, batch.delta_delta, **TOL)


def test_streaming_matches_batch_pathological_chunks(sine_440, tlog):
    x, sr, input_id = sine_440
    cfg = MFCCConfig(sample_rate=sr)
    batch = extract_features(x, cfg)
    # One sample per chunk for the first 500, then the rest at once.
    sizes = [1] * 500 + [len(x) - 500]
    mfcc, delta, delta2 = _run_stream(x, cfg, sizes)
    tlog.step("stream_pathological", input_id=input_id,
              basis="1-sample chunks then bulk == batch",
              n_chunks=len(sizes))
    np.testing.assert_allclose(mfcc, batch.mfcc, **TOL)
    np.testing.assert_allclose(delta, batch.delta, **TOL)
    np.testing.assert_allclose(delta2, batch.delta_delta, **TOL)


def test_no_future_leakage_prefix_property(white_noise, tlog):
    """Frames emitted after each chunk must equal batch features computed on
    the samples delivered *so far* — nothing may arrive early."""
    x, sr, input_id = white_noise
    cfg = MFCCConfig(sample_rate=sr)
    stream = StreamingMFCC(cfg)
    chunk_sizes = [500, 700, 300, 2000, 1000]
    delivered = 0
    emitted_rows = []
    for i, size in enumerate(chunk_sizes):
        chunk = x[delivered : delivered + size]
        delivered += len(chunk)
        emit = stream.accept_chunk(chunk)
        emitted_rows.append(emit.mfcc)
        so_far = np.vstack(emitted_rows)
        # Batch on the prefix alone: frames fully inside the prefix must be
        # a prefix of what the stream has emitted (the stream legitimately
        # lags by 2*delta_width frames of context).
        prefix_batch = extract_features(x[:delivered], cfg)
        n_check = min(len(so_far), prefix_batch.n_frames)
        tlog.step("prefix_check", input_id=input_id,
                  basis="emitted frames == batch(prefix) frames, no lookahead",
                  chunk=i, delivered=delivered, emitted=int(len(so_far)),
                  prefix_frames=prefix_batch.n_frames, checked=n_check)
        assert len(so_far) <= prefix_batch.n_frames, (
            "stream emitted frames beyond what the delivered samples justify"
        )
        np.testing.assert_allclose(
            so_far[:n_check], prefix_batch.mfcc[:n_check], **TOL
        )


def test_streaming_short_input_emits_nothing_until_finalize(short_input, tlog):
    x, sr, input_id = short_input
    cfg = MFCCConfig(sample_rate=sr)
    stream = StreamingMFCC(cfg)
    emit = stream.accept_chunk(x)
    final = stream.finalize()
    tlog.step("stream_short", input_id=input_id,
              basis="120 samples < 400 -> zero frames ever; finalize empty too",
              chunk_frames=emit.n_frames, final_frames=final.n_frames)
    assert emit.n_frames == 0
    assert final.n_frames == 0
    assert stream.frames_emitted == 0


def test_streaming_state_errors(white_noise, tlog):
    x, sr, input_id = white_noise
    stream = StreamingMFCC(MFCCConfig(sample_rate=sr))
    stream.accept_chunk(x[:1000])
    stream.finalize()
    with pytest.raises(StreamStateError):
        stream.accept_chunk(x[1000:2000])
    with pytest.raises(StreamStateError):
        stream.finalize()
    tlog.step("stream_state_errors", input_id=input_id,
              basis="chunk/finalize after finalize -> StreamStateError")


def test_streaming_emission_lag_is_bounded(sine_440, tlog):
    """Emission may lag by at most 2*delta_width frames of context."""
    x, sr, input_id = sine_440
    cfg = MFCCConfig(sample_rate=sr)
    n_frames = extract_features(x, cfg).n_frames
    stream = StreamingMFCC(cfg)
    stream.accept_chunk(x)  # whole second at once
    lag = n_frames - stream.frames_emitted
    tlog.step("emission_lag", input_id=input_id,
              basis="lag == 2*delta_width before finalize",
              lag=lag, want=2 * cfg.delta_width)
    assert lag == 2 * cfg.delta_width
    final = stream.finalize()
    assert stream.frames_emitted == n_frames
    assert final.n_frames == 2 * cfg.delta_width
