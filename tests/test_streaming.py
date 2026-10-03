"""Tests for frame-wise streaming analysis / synthesis.

The streaming path is checked against the *batch* path, and the batch
path itself is independently verified in ``test_algorithms`` — so these
tests are not circular.
"""

from __future__ import annotations

import numpy as np
import pytest

from stft_backend.errors import ErrorCode, StftError
from stft_backend.numeric import validate_transform_params
from stft_backend.streaming import (
    DIRECTION_ANALYZE,
    DIRECTION_SYNTHESIZE,
    SessionStore,
    StreamAnalyzer,
    StreamSynthesizer,
)

pytestmark = pytest.mark.unit


def _resolved(nperseg: int, hop: int, nfft: int | None = None):
    nperseg, hop, nfft, window = validate_transform_params(
        nperseg, hop, nfft, "hann"
    )
    return nperseg, hop, nfft, window


def _complex_pairs(column: np.ndarray) -> list[list[float]]:
    return [[float(z.real), float(z.imag)] for z in column]


@pytest.mark.parametrize("nperseg,hop", [(8, 4), (7, 3)])
def test_chunked_analysis_matches_batch(nperseg: int, hop: int) -> None:
    rng = np.random.default_rng(101)
    x = rng.standard_normal(37)
    nperseg, hop, nfft, window = _resolved(nperseg, hop)

    analyzer = StreamAnalyzer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    frames = []
    for chunk in (x[:5], x[5:13], x[13:14], x[14:]):
        frames.extend(analyzer.push(chunk))
    frames.extend(analyzer.finish())

    from stft_backend import algorithms

    spec, meta = algorithms.stft(
        x, nperseg=nperseg, hop=hop, nfft=nfft, window=window
    )
    assert len(frames) == meta.n_frames
    for m, frame in enumerate(frames):
        assert frame["frame_index"] == m
        assert frame["start_sample"] == m * hop - nperseg // 2
        assert frame["center_sample"] == m * hop
        column = np.array([complex(*pair) for pair in frame["bins"]])
        np.testing.assert_allclose(column, spec[:, m], rtol=1e-12, atol=1e-12)
    assert analyzer.input_length == 37


def test_analysis_then_synthesis_stream_roundtrip() -> None:
    rng = np.random.default_rng(202)
    x = rng.standard_normal(41)
    nperseg, hop, nfft, window = _resolved(8, 3)

    analyzer = StreamAnalyzer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    frames = analyzer.push(x[:20]) + analyzer.push(x[20:])
    frames += analyzer.finish()
    for frame in frames:
        location = synth.push_frame(frame["frame_index"], frame["bins"])
        assert location["frame_index"] == frame["frame_index"]
    y = synth.finish(signal_length=x.size)
    assert y.shape == x.shape
    np.testing.assert_allclose(y, x, rtol=1e-10, atol=1e-10)


def test_synthesis_rejects_out_of_order_frames() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    with pytest.raises(StftError) as excinfo:
        synth.push_frame(1, _complex_pairs(np.zeros(5, dtype=np.complex128)))
    assert excinfo.value.code is ErrorCode.FRAME_SEQUENCE_ERROR
    assert excinfo.value.details == {"expected": 0, "got": 1}


def test_synthesis_rejects_duplicate_frame() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    zeros = _complex_pairs(np.zeros(5, dtype=np.complex128))
    synth.push_frame(0, zeros)
    with pytest.raises(StftError) as excinfo:
        synth.push_frame(0, zeros)
    assert excinfo.value.code is ErrorCode.FRAME_SEQUENCE_ERROR


def test_synthesis_rejects_wrong_bin_count() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    with pytest.raises(StftError) as excinfo:
        synth.push_frame(0, _complex_pairs(np.zeros(4, dtype=np.complex128)))
    assert excinfo.value.code is ErrorCode.SPECTRUM_SHAPE_MISMATCH
    assert excinfo.value.details["expected_bins"] == 5
    assert excinfo.value.details["got_bins"] == 4


def test_finishing_empty_synthesis_is_rejected() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    with pytest.raises(StftError) as excinfo:
        synth.finish(signal_length=8)
    assert excinfo.value.code is ErrorCode.FRAME_SEQUENCE_ERROR


def test_uncovered_stream_finish_reports_denominator_failure() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    synth = StreamSynthesizer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    zeros = _complex_pairs(np.zeros(5, dtype=np.complex128))
    # Two frames give an inferred coverage of hop = 4 original samples;
    # ask for 10 so the OLA denominator is zero in the requested region.
    synth.push_frame(0, zeros)
    synth.push_frame(1, zeros)
    with pytest.raises(StftError) as excinfo:
        synth.finish(signal_length=10)
    assert excinfo.value.code is ErrorCode.UNCOVERED_SAMPLES
    assert excinfo.value.details["requested_length"] == 10
    assert excinfo.value.details["first_uncovered_sample"] >= 4


def test_analyzer_rejects_non_finite_chunk() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    analyzer = StreamAnalyzer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    with pytest.raises(StftError) as excinfo:
        analyzer.push(np.array([1.0, float("inf"), 3.0]))
    assert excinfo.value.code is ErrorCode.NON_FINITE_SIGNAL


def test_push_after_finish_is_rejected() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    analyzer = StreamAnalyzer(
        nperseg=nperseg, hop=hop, nfft=nfft, window=window, onesided=True
    )
    analyzer.finish()
    with pytest.raises(StftError) as excinfo:
        analyzer.push(np.ones(3))
    assert excinfo.value.code is ErrorCode.FRAME_SEQUENCE_ERROR


def test_session_store_direction_conflict_and_lookup() -> None:
    nperseg, hop, nfft, window = _resolved(8, 4)
    store = SessionStore()
    session = store.create(
        direction=DIRECTION_ANALYZE, nperseg=nperseg, hop=hop, nfft=nfft,
        window=window, onesided=True,
    )
    with pytest.raises(StftError) as excinfo:
        store.get(session.session_id, expected_direction=DIRECTION_SYNTHESIZE)
    assert excinfo.value.code is ErrorCode.SESSION_DIRECTION_CONFLICT
    assert len(store) == 1
    store.close(session.session_id)
    with pytest.raises(StftError) as excinfo:
        store.get(session.session_id)
    assert excinfo.value.code is ErrorCode.SESSION_NOT_FOUND
    with pytest.raises(StftError) as excinfo:
        store.get("missing")
    assert excinfo.value.code is ErrorCode.SESSION_NOT_FOUND
