"""Streaming state machine: chunked input must reproduce the batch result
exactly, emit deltas only with sufficient future context (no leakage of
not-yet-arrived samples), and fail loudly on lifecycle violations."""

import numpy as np
import pytest

from mfcc_backend import fixtures
from mfcc_backend.config import MFCCConfig
from mfcc_backend.errors import (
    InsufficientSignalError,
    InvalidAudioError,
    SessionStateError,
)
from mfcc_backend.pipeline import compute_pipeline
from mfcc_backend.streaming import StreamingMFCC

from conftest import signal_id

CFG = MFCCConfig().validate()
N = CFG.delta_width


def _run_stream(x, chunk_sizes):
    stream = StreamingMFCC(CFG)
    out = {"mfcc": [], "delta": [], "delta2": []}
    pos = 0
    for size in chunk_sizes:
        if pos >= len(x):
            break
        emission = stream.accept_chunk(x[pos : pos + size])
        for k in out:
            out[k].append(emission.__dict__[k])
        pos += size
    if pos < len(x):
        emission = stream.accept_chunk(x[pos:])
        for k in out:
            out[k].append(emission.__dict__[k])
    emission = stream.finish()
    for k in out:
        out[k].append(emission.__dict__[k])
    return {k: np.concatenate([b for b in blocks if len(b)], axis=0) for k, blocks in out.items()}


@pytest.mark.parametrize(
    "chunk_sizes",
    [
        [16000],  # single shot
        [160],  # exactly one hop per chunk
        [1],  # pathological: one sample per chunk (uses first 800 only via loop below)
        [333, 7, 400, 1000, 259],  # irregular, crosses frame boundaries
        [400, 160],  # frame-aligned
    ],
)
def test_stream_equals_batch(chunk_sizes, run_log):
    x = fixtures.make_white_noise(duration_s=0.25, seed=11)  # 4000 samples
    if chunk_sizes == [1]:
        # keep the 1-sample test bounded but still multi-chunk
        chunk_sizes = [1] * 500 + [3500]
    batch = compute_pipeline(x, CFG)
    got = _run_stream(x, chunk_sizes)
    assert got["mfcc"].shape == batch.mfcc.shape
    assert got["delta"].shape == batch.delta.shape
    assert got["delta2"].shape == batch.delta2.shape
    np.testing.assert_allclose(got["mfcc"], batch.mfcc, rtol=0, atol=1e-12)
    np.testing.assert_allclose(got["delta"], batch.delta, rtol=0, atol=1e-12)
    np.testing.assert_allclose(got["delta2"], batch.delta2, rtol=0, atol=1e-12)
    run_log("stream_equals_batch", input_id=signal_id(x), chunk_sizes=chunk_sizes[:6],
            n_frames=batch.n_frames, verdict="pass",
            rationale="concatenated stream output equals batch output at atol=1e-12")


def test_no_future_leakage_and_incremental_correctness(run_log):
    """After each chunk, every emitted row must already equal the final
    batch row at the same index — i.e. emitted rows never depend on
    samples that have not arrived yet."""
    x = fixtures.make_sine(440.0, duration_s=0.5)
    batch = compute_pipeline(x, CFG)
    stream = StreamingMFCC(CFG)
    rng = np.random.default_rng(3)
    pos, seen_mfcc, seen_delta = 0, 0, 0
    while pos < len(x):
        size = int(rng.integers(50, 900))
        emission = stream.accept_chunk(x[pos : pos + size])
        pos += size
        seen_mfcc += len(emission.mfcc)
        seen_delta += len(emission.delta)
        # mfcc rows are final immediately and must match batch prefix rows
        np.testing.assert_allclose(
            np.asarray(stream._mfcc_rows)[:seen_mfcc], batch.mfcc[:seen_mfcc],
            rtol=0, atol=1e-12)
        # delta may lag by at most N frames, never lead
        assert seen_delta <= max(0, seen_mfcc - N)
        if seen_delta:
            np.testing.assert_allclose(
                np.asarray(stream._delta_rows)[:seen_delta], batch.delta[:seen_delta],
                rtol=0, atol=1e-12)
    stream.finish()
    assert seen_mfcc == batch.n_frames
    run_log("no_future_leakage", input_id=signal_id(x), frames=seen_mfcc,
            verdict="pass",
            rationale="emitted rows equal final batch rows; delta lags mfcc by >=N")


def test_trailing_partial_frame_dropped_like_batch(run_log):
    x = fixtures.make_sine(440.0, duration_s=0.5)
    batch = compute_pipeline(x, CFG)
    stream = StreamingMFCC(CFG)
    stream.accept_chunk(x)
    final = stream.finish()
    totals = stream.totals()
    assert totals["frames"] == batch.n_frames
    assert totals["buffered_samples"] == 0
    # the dropped tail is exactly the incomplete remainder
    dropped = len(x) - ((batch.n_frames - 1) * CFG.hop_length + CFG.frame_length)
    assert dropped == len(x) % CFG.hop_length or dropped < CFG.frame_length
    assert len(final.delta) > 0  # held-back delta rows released at finish
    run_log("partial_tail_dropped", dropped_tail_samples=dropped,
            frames=batch.n_frames, verdict="pass",
            rationale="finish() drops the incomplete frame exactly like the batch framer")


def test_chunk_after_finish_rejected():
    stream = StreamingMFCC(CFG)
    stream.accept_chunk(fixtures.make_sine(duration_s=0.1))
    stream.finish()
    with pytest.raises(SessionStateError):
        stream.accept_chunk(np.zeros(160))
    with pytest.raises(SessionStateError):
        stream.finish()  # second finish is also an error, not a cached success


def test_empty_chunk_rejected():
    stream = StreamingMFCC(CFG)
    with pytest.raises(InvalidAudioError):
        stream.accept_chunk([])


def test_nan_chunk_rejected():
    stream = StreamingMFCC(CFG)
    with pytest.raises(InvalidAudioError):
        stream.accept_chunk([0.0, float("nan"), 1.0])


def test_too_short_stream_fails_at_finish(run_log):
    stream = StreamingMFCC(CFG)
    stream.accept_chunk(fixtures.make_short(n_samples=100))
    with pytest.raises(InsufficientSignalError) as excinfo:
        stream.finish()
    run_log("short_stream_fails", error_code=excinfo.value.code, verdict="pass",
            rationale="0 complete frames at finish is INSUFFICIENT_SIGNAL, not empty success")
