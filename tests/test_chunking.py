"""Chunking contract: any contiguous partition of a signal must produce
bit-identical output to processing it whole, because per-sample operation
order is chunk-invariant and state is carried explicitly.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from app.dsp.coefficients import normalize_and_validate
from app.dsp.filter import SOSCascadeFilter

SEED = 20261003


def _random_partition(rng: np.random.Generator, n: int) -> list[int]:
    cuts = sorted(rng.choice(np.arange(1, n), size=7, replace=False).tolist())
    bounds = [0, *cuts, n]
    return [b - a for a, b in zip(bounds, bounds[1:])]


@pytest.mark.parametrize("trial", range(5))
def test_random_chunking_equals_whole(trial, run_log):
    rng = np.random.default_rng(SEED + trial)
    sos = butter(5, [0.05, 0.45], btype="bandpass", output="sos")
    norm = normalize_and_validate(sos)
    n_channels, n_samples = 3, 997
    x = rng.standard_normal((n_channels, n_samples))

    whole = SOSCascadeFilter(norm, n_channels).process(x)

    chunked_filter = SOSCascadeFilter(norm, n_channels)
    sizes = _random_partition(rng, n_samples)
    parts = []
    pos = 0
    for size in sizes:
        parts.append(chunked_filter.process(x[:, pos:pos + size]))
        pos += size
    chunked = np.concatenate(parts, axis=1)

    identical = bool(np.array_equal(whole, chunked))
    run_log.log("chunk_compare", seed=SEED + trial, chunk_sizes=sizes,
                bit_identical=identical,
                verdict="pass" if identical else "fail")
    assert identical, "chunked output differs from whole-run output"


def test_sample_at_a_time_equals_whole(run_log):
    sos = butter(3, 0.2, output="sos")
    norm = normalize_and_validate(sos)
    rng = np.random.default_rng(SEED)
    x = rng.standard_normal((2, 200))

    whole = SOSCascadeFilter(norm, 2).process(x)
    trickle = SOSCascadeFilter(norm, 2)
    out = np.column_stack(
        [trickle.process(x[:, i:i + 1]) for i in range(x.shape[1])]
    )
    assert np.array_equal(whole, out)
    run_log.log("sample_at_a_time", verdict="pass")


def test_chunked_matches_scipy_oneshot(run_log):
    # Cross-check chunked streaming against scipy's independent one-shot path.
    rng = np.random.default_rng(SEED)
    sos = butter(4, 0.3, output="sos")
    norm = normalize_and_validate(sos)
    x = rng.standard_normal((1, 1000))
    filt = SOSCascadeFilter(norm, 1)
    y = np.concatenate(
        [filt.process(x[:, i:i + 250]) for i in range(0, 1000, 250)], axis=1
    )
    err = float(np.max(np.abs(y[0] - sosfilt(sos, x[0]))))
    run_log.log("chunked_vs_scipy", max_err=err, verdict="pass" if err < 1e-10 else "fail")
    assert err < 1e-10


def test_empty_chunk_is_noop():
    sos = butter(2, 0.2, output="sos")
    norm = normalize_and_validate(sos)
    filt = SOSCascadeFilter(norm, 1)
    y0 = filt.process(np.zeros((1, 0)))
    assert y0.shape == (1, 0)
    x = np.ones((1, 32))
    assert np.array_equal(filt.process(x), SOSCascadeFilter(norm, 1).process(x))
