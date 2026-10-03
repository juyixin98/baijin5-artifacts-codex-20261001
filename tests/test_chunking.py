"""Chunked vs whole-block equivalence, across channels and filters."""

import numpy as np
import scipy.signal

from app.dsp.cascade import SosCascadeFilter
from app.dsp.coefficients import normalize_and_validate

LIMITS = dict(max_sections=32, pole_radius_limit=1.0)

CHUNK_PATTERNS = [
    [1] * 364,
    [7, 1, 63, 2, 128, 3, 160],
    [100, 200, 64],
    [364],
]


def _sos():
    return normalize_and_validate(
        scipy.signal.butter(4, [0.1, 0.3], btype="band", output="sos"),
        **LIMITS,
    ).sections


def test_chunked_equals_whole_bitwise(runlog, rng):
    sos = _sos()
    n = 364
    x = rng.standard_normal((n, 2))
    whole = SosCascadeFilter(sos, 2).process_block(x)

    for pattern in CHUNK_PATTERNS:
        assert sum(pattern) == n
        filt = SosCascadeFilter(sos, 2)
        parts, pos = [], 0
        for size in pattern:
            parts.append(filt.process_block(x[pos:pos + size]))
            pos += size
        chunked = np.concatenate(parts, axis=0)
        identical = bool(np.array_equal(chunked, whole))
        runlog("chunk_compare", pattern=pattern, bitwise_identical=identical,
               rationale="per-sample op order is chunk-invariant, so "
                         "outputs must be bitwise equal, not just close")
        assert identical, f"chunk pattern {pattern} diverged"


def test_chunked_matches_python_reference(runlog, rng):
    from tests.reference import reference_sos_filter

    sos = _sos()
    n = 200
    x = rng.standard_normal((n, 1))
    filt = SosCascadeFilter(sos, 1)
    got = np.concatenate(
        [filt.process_block(x[i:i + 13]) for i in range(0, n, 13)], axis=0
    )
    ref = reference_sos_filter(sos, x)
    err = float(np.max(np.abs(got - ref)))
    runlog("chunk_vs_reference", chunk=13, n=n, max_abs_err=err,
           tolerance=1e-12)
    assert err < 1e-12
