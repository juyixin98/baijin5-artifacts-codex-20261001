"""Framing stage: pre-emphasis, frame counts, window — against reference."""

import numpy as np
import pytest

from mfcc_backend.framing import (
    frame_count,
    frame_signal,
    hamming_window,
    preemphasis,
)

from reference_impl import ref_frame_count, ref_frames, ref_hamming, ref_preemphasis


def test_preemphasis_matches_reference(white_noise, tlog):
    x, sr, input_id = white_noise
    got = preemphasis(x[:1000], 0.97)
    want = np.array(ref_preemphasis(x[:1000].tolist(), 0.97))
    tlog.step("preemphasis", input_id=input_id,
              basis="elementwise vs literal-loop reference, atol=1e-15",
              max_abs_err=float(np.max(np.abs(got - want))))
    np.testing.assert_allclose(got, want, atol=1e-15)


def test_preemphasis_first_sample_and_initial(tlog):
    x = np.array([0.5, 0.25, -0.25])
    got = preemphasis(x, 0.97)
    tlog.step("preemphasis_edge", basis="y[0]=x[0]; y[n]=x[n]-0.97*x[n-1]",
              got=got.tolist())
    np.testing.assert_allclose(got, [0.5, 0.25 - 0.97 * 0.5, -0.25 - 0.97 * 0.25])
    got_init = preemphasis(x, 0.97, initial=1.0)
    assert got_init[0] == pytest.approx(0.5 - 0.97 * 1.0)


def test_frame_count_boundaries(tlog):
    cases = [(0, 400, 160, 0), (399, 400, 160, 0), (400, 400, 160, 1),
             (559, 400, 160, 1), (560, 400, 160, 2), (16000, 400, 160, 98)]
    for n, fl, hop, want in cases:
        assert frame_count(n, fl, hop) == want == ref_frame_count(n, fl, hop)
    tlog.step("frame_count", basis="1+(N-L)//H for N>=L else 0", cases=cases)


def test_frame_signal_shape_and_content(white_noise, tlog):
    x, sr, input_id = white_noise
    frames = frame_signal(x, 400, 160)
    want = np.array(ref_frames(x.tolist(), 400, 160))
    tlog.step("frame_signal", input_id=input_id,
              basis="frame t == samples[t*160 : t*160+400]; 98 frames for 1s@16k",
              shape=frames.shape)
    assert frames.shape == (98, 400)
    np.testing.assert_array_equal(frames, want)


def test_frame_signal_short_input_returns_empty(short_input, tlog):
    x, sr, input_id = short_input
    frames = frame_signal(x, 400, 160)
    tlog.step("frame_short", input_id=input_id,
              basis="120 samples < 400 frame -> (0, 400), not an error",
              shape=frames.shape)
    assert frames.shape == (0, 400)


def test_hamming_window_matches_reference(tlog):
    got = hamming_window(400)
    want = np.array(ref_hamming(400))
    tlog.step("hamming", basis="symmetric 0.54-0.46cos(2πk/(N-1)), endpoints=0.08",
              w0=float(got[0]), w_max=float(got.max()))
    np.testing.assert_allclose(got, want, atol=1e-15)
    assert got[0] == pytest.approx(0.08)
    # Even-length symmetric window peaks between the two center samples.
    assert got.max() == pytest.approx(1.0, abs=1e-3)
    assert got[0] == got[-1]  # symmetric, not periodic
