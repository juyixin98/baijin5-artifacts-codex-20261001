"""Stateful filters: SciPy cross-check, state correspondence, exact round-trip."""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from app.lpc.filters import (
    AnalysisFilter,
    SynthesisFilter,
    corresponding_synthesis_state,
)

A = np.array([1.0, -0.9, 0.5, -0.2])  # stable, poles inside unit circle
ORDER = len(A) - 1


def _signal(n: int = 500) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal(n)


def test_analysis_matches_scipy_lfilter_zero_state():
    x = _signal()
    residual = AnalysisFilter(ORDER).process(x, A)
    np.testing.assert_allclose(residual, lfilter(A, [1.0], x), rtol=1e-12, atol=1e-12)


def test_synthesis_matches_scipy_lfilter_zero_state():
    e = _signal()
    out = SynthesisFilter(ORDER).process(e, A)
    np.testing.assert_allclose(out, lfilter([1.0], A, e), rtol=1e-10, atol=1e-10)


def test_roundtrip_is_exact_in_one_shot():
    x = _signal()
    e = AnalysisFilter(ORDER).process(x, A)
    x_hat = SynthesisFilter(ORDER).process(e, A)
    np.testing.assert_allclose(x_hat, x, rtol=1e-10, atol=1e-12)


def test_streaming_equals_one_shot_when_states_correspond():
    x = _signal()
    frames = [x[:200], x[200:350], x[350:]]

    analysis = AnalysisFilter(ORDER)
    synthesis = SynthesisFilter(ORDER)
    reconstructed = []
    for frame in frames:
        e = analysis.process(frame, A)
        # The synthesis filter's carried state corresponds to the analysis
        # filter's state, so frame-wise reconstruction is exact.
        reconstructed.append(synthesis.process(e, A))
    x_hat = np.concatenate(reconstructed)
    np.testing.assert_allclose(x_hat, x, rtol=1e-10, atol=1e-12)

    # And the streamed residual equals the one-shot residual.
    e_one_shot = AnalysisFilter(ORDER).process(x, A)
    analysis2 = AnalysisFilter(ORDER)
    e_streamed = np.concatenate([analysis2.process(f, A) for f in frames])
    np.testing.assert_allclose(e_streamed, e_one_shot, rtol=1e-12, atol=1e-12)


def test_corresponding_synthesis_state_is_analysis_history():
    x = _signal(100)
    analysis = AnalysisFilter(ORDER)
    analysis.process(x, A)
    state = corresponding_synthesis_state(analysis)
    np.testing.assert_array_equal(state, x[-ORDER:])


def test_wrong_initial_state_breaks_reconstruction():
    x = _signal()
    boundary = 250
    analysis = AnalysisFilter(ORDER)
    analysis.process(x[:boundary], A)
    e2 = analysis.process(x[boundary:], A)

    # Correct: synthesis initialised with the corresponding state.
    analysis2 = AnalysisFilter(ORDER)
    analysis2.process(x[:boundary], A)
    state = corresponding_synthesis_state(analysis2)
    analysis2.process(x[boundary:], A)
    x_good = SynthesisFilter(ORDER, state).process(e2, A)
    np.testing.assert_allclose(x_good, x[boundary:], rtol=1e-10, atol=1e-12)

    # Wrong: zero initial state must NOT reconstruct the second frame.
    x_bad = SynthesisFilter(ORDER).process(e2, A)
    err = np.linalg.norm(x_bad - x[boundary:]) / np.linalg.norm(x[boundary:])
    assert err > 1e-3, "zero initial state should visibly corrupt reconstruction"


def test_state_length_validation():
    try:
        SynthesisFilter(ORDER, np.zeros(ORDER + 1))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for mismatched initial state")
