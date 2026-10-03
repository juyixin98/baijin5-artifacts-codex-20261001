"""Bootstrap CI tests.

The reference interval is produced by an independent reimplementation of the
resampling loop written directly in this test file (numpy Generator +
math.log), not by calling seqdist.bootstrap or seqdist.distance. Exact
equality is expected because both draw from the same seeded generator
contract (rng.integers(0, n, size=n) per replicate, sequential loop).
"""
import math

import numpy as np
import pytest

from seqdist.bootstrap import bootstrap_ci
from seqdist.distance import EstimateStatus, classify_sites
from seqdist.parsing import parse_pair


def codes_of(s1: str, s2: str) -> np.ndarray:
    stats = classify_sites(parse_pair(s1, s2))
    return np.asarray(stats.site_codes, dtype=np.int64)


def reference_jc69_bootstrap(codes: np.ndarray, n_replicates: int, alpha: float, seed: int):
    """Independent reference: resample, correct by hand, take percentiles."""
    rng = np.random.default_rng(seed)
    n = codes.size
    vals = []
    n_sat = 0
    for _ in range(n_replicates):
        idx = rng.integers(0, n, size=n)
        p = float(np.mean(codes[idx] != 0))
        arg = 1.0 - 4.0 * p / 3.0
        if arg <= 0.0:
            n_sat += 1
        else:
            vals.append(-0.75 * math.log(arg))
    lo, hi = np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi), n_sat


CODES = codes_of("ACGTACGTACGTACGTACGT" "ACGTACGTACGTACGTACGT",
                 "ACGTACGTACGTACGTACGT" "AGGTAAGTACCTACGTACGT")


def test_bootstrap_matches_independent_reference_interval():
    res = bootstrap_ci(CODES, "jc69", n_replicates=500, alpha=0.05, seed=42)
    lo, hi, n_sat = reference_jc69_bootstrap(CODES, 500, 0.05, 42)
    assert res.status is EstimateStatus.OK
    assert res.n_saturated == n_sat == 0
    assert res.lower == pytest.approx(lo, rel=0, abs=1e-15)
    assert res.upper == pytest.approx(hi, rel=0, abs=1e-15)
    assert res.lower <= res.upper


def test_bootstrap_is_reproducible_with_fixed_seed():
    a = bootstrap_ci(CODES, "k80", n_replicates=200, alpha=0.1, seed=7)
    b = bootstrap_ci(CODES, "k80", n_replicates=200, alpha=0.1, seed=7)
    assert (a.lower, a.upper, a.n_ok, a.n_saturated) == (
        b.lower, b.upper, b.n_ok, b.n_saturated)


def test_bootstrap_differs_with_different_seed():
    a = bootstrap_ci(CODES, "jc69", n_replicates=200, alpha=0.05, seed=1)
    b = bootstrap_ci(CODES, "jc69", n_replicates=200, alpha=0.05, seed=2)
    assert (a.lower, a.upper) != (b.lower, b.upper)


def test_identical_sequences_give_degenerate_zero_interval():
    codes = codes_of("ACGTACGT", "ACGTACGT")
    res = bootstrap_ci(codes, "jc69", n_replicates=100, alpha=0.05, seed=3)
    assert res.status is EstimateStatus.OK
    assert res.lower == 0.0 and res.upper == 0.0


def test_saturated_replicates_are_counted_not_clamped():
    # p = 0.7 with 20 sites: resamples frequently cross the 0.75 boundary.
    codes = codes_of("A" * 20, "A" * 6 + "G" * 5 + "C" * 5 + "T" * 4)
    res = bootstrap_ci(codes, "jc69", n_replicates=400, alpha=0.05, seed=11)
    assert res.n_ok + res.n_saturated == 400
    assert res.n_saturated > 0
    # interval is computed from valid replicates only and stays finite
    assert res.status is EstimateStatus.OK
    assert res.lower is not None and res.upper is not None
    # every valid JC69 replicate of a p<=0.75 sample is < +inf and >= 0
    assert res.lower >= 0.0


def test_all_saturated_replicates_are_non_estimable():
    # p = 1.0: every resample is outside the JC69 domain.
    codes = codes_of("AAAA", "CCCC")
    res = bootstrap_ci(codes, "jc69", n_replicates=50, alpha=0.05, seed=5)
    assert res.status is EstimateStatus.NON_ESTIMABLE
    assert res.lower is None and res.upper is None
    assert res.n_saturated == 50


def test_zero_replicates_are_non_estimable():
    res = bootstrap_ci(CODES, "p", n_replicates=0, alpha=0.05, seed=0)
    assert res.status is EstimateStatus.NON_ESTIMABLE
    assert res.n_ok == 0
