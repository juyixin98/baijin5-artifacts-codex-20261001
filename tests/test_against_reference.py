"""Cross-check the production core against the independent pure-Python
reference implementation in tests/reference_impl.py."""

from __future__ import annotations

import numpy as np
import pytest

from app.algorithms.lms import AdaptiveFilter, FilterSpec
from app.config import Settings
from tests.reference_impl import lms_reference, nlms_reference

ATOL = 1e-12


def run_case(algorithm, mu, epsilon, n, seed, freeze_mask=None):
    rng = np.random.default_rng(seed)
    length = 8
    xs = rng.standard_normal(n)
    ds = rng.standard_normal(n)
    w0 = rng.standard_normal(length)
    buf0 = rng.standard_normal(length)

    spec = FilterSpec(algorithm=algorithm, filter_length=length, mu=mu, epsilon=epsilon)
    f = AdaptiveFilter(spec, Settings())
    f._w = w0.copy()
    f._buf = buf0.copy()
    result = f.process_block(xs, ds, freeze_mask=freeze_mask)

    ref_fn = lms_reference if algorithm == "lms" else (
        lambda w, b, m, x, d, fz: nlms_reference(w, b, m, epsilon, x, d, fz)
    )
    w_ref, buf_ref, y_ref, e_ref = ref_fn(
        w0.tolist(), buf0.tolist(), mu, xs.tolist(), ds.tolist(),
        None if freeze_mask is None else freeze_mask.tolist(),
    )
    return f, result, (w_ref, buf_ref, y_ref, e_ref)


@pytest.mark.parametrize("algorithm,mu", [("lms", 0.05), ("nlms", 0.7)])
def test_multi_step_trajectory_matches_reference(algorithm, mu):
    f, result, (w_ref, buf_ref, y_ref, e_ref) = run_case(
        algorithm, mu, epsilon=1e-6, n=500, seed=42
    )
    np.testing.assert_allclose(f.weights(), w_ref, atol=ATOL)
    np.testing.assert_allclose(f.buffer(), buf_ref, atol=ATOL)
    np.testing.assert_allclose(result.outputs, y_ref, atol=ATOL)
    np.testing.assert_allclose(result.errors, e_ref, atol=ATOL)


def test_freeze_mask_matches_reference():
    rng = np.random.default_rng(5)
    freeze_mask = rng.random(300) < 0.4
    f, result, (w_ref, _, y_ref, e_ref) = run_case(
        "nlms", 0.9, epsilon=1e-8, n=300, seed=9, freeze_mask=freeze_mask
    )
    np.testing.assert_allclose(f.weights(), w_ref, atol=ATOL)
    np.testing.assert_allclose(result.outputs, y_ref, atol=ATOL)
    np.testing.assert_allclose(result.errors, e_ref, atol=ATOL)
    assert result.frozen_samples == int(np.count_nonzero(freeze_mask))


def test_block_splitting_is_streaming_consistent():
    """One block of N samples must equal two consecutive blocks."""
    rng = np.random.default_rng(13)
    xs, ds = rng.standard_normal(200), rng.standard_normal(200)
    spec = FilterSpec(algorithm="nlms", filter_length=16, mu=0.5, epsilon=1e-8)

    whole = AdaptiveFilter(spec, Settings())
    r_whole = whole.process_block(xs, ds)

    split = AdaptiveFilter(spec, Settings())
    r1 = split.process_block(xs[:120], ds[:120])
    r2 = split.process_block(xs[120:], ds[120:])

    np.testing.assert_allclose(split.weights(), whole.weights(), atol=ATOL)
    np.testing.assert_allclose(
        np.concatenate([r1.outputs, r2.outputs]), r_whole.outputs, atol=ATOL
    )
    np.testing.assert_allclose(
        np.concatenate([r1.errors, r2.errors]), r_whole.errors, atol=ATOL
    )
