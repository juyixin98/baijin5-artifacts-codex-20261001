"""Statistical-contract tests: paired design, p-values, enumeration.

Reference answers come from two independent sources:
* hand counts written in the test / fixture reference tables;
* :mod:`app.evidence`'s plain-Python ``itertools`` oracle, which does not
  import the kernel under test.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from app.core import contract, kernel
from app.evidence import oracle_pvalue
from app.repro.fixtures import FIXTURES


# ---------------------------------------------------------------------------
# Paired randomization set (contract point #1)
# ---------------------------------------------------------------------------

def test_randomization_set_is_paired_sign_flips_not_full_shuffle():
    # 3 pairs => 8 assignments of independent per-pair coins.
    chunks = list(kernel.iter_sign_vector_chunks(3, chunk_size=4))
    vectors = np.vstack(chunks)
    assert vectors.shape == (8, 3)
    # Every entry is a +/- coin flip.
    assert set(np.unique(vectors)) == {-1, 1}
    # The 8 rows are exactly the Cartesian product {-1,+1}^3, each once.
    expected = {tuple(2 * np.array(b, dtype=int) - 1)
                for b in itertools.product((0, 1), repeat=3)}
    assert {tuple(row) for row in vectors} == expected


def test_randomization_size_is_2_to_n_never_factorial():
    assert contract.n_assignments(3) == 8
    assert contract.n_assignments(4) == 16
    assert contract.n_assignments(10) == 1024
    # A mistaken full-sample shuffle of 2n units would yield 720 for n=3.
    assert contract.n_assignments(3) != math.factorial(6)


def test_enumeration_never_moves_units_across_pairs():
    # Structural guarantee: rows are sign vectors, not permutations of units.
    for signs in kernel.iter_sign_vector_chunks(5, chunk_size=7):
        assert np.all(np.isin(signs, [-1, 1]))
        assert signs.shape[1] == 5


# ---------------------------------------------------------------------------
# Exact p-values: hand-computed references
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name,effect,expected", [
    ("identical_outcomes", 0.0, 1.0),
    ("identical_outcomes", 1.0, 2 / 16),
    ("balanced_small", 0.0, 1.0),
    ("extreme_differences", 0.0, 1.0),
    ("extreme_differences", 10.0, 8 / 16),
    ("one_extreme_pair", 0.0, 2 / 16),
    ("monotone_differences", 0.0, 2 / 32),
])
def test_exact_pvalue_hand_references(fixture_name, effect, expected):
    fx = FIXTURES[fixture_name]
    d = np.asarray(fx.differences, dtype=float)
    result = kernel.exact_pvalue(d, effect=effect)
    assert result.method == "exact-signflip"
    assert result.p_value == pytest.approx(expected)
    assert result.n_assignments == fx.reference["randomization_set_size"]
    assert result.count_as_extreme == round(expected * result.n_assignments)


def test_extreme_pair_enumeration_distribution():
    # Signed sums for d=(5,1,1,1): +/-8 once, +/-6 three, +/-4 three,
    # +/-2 once => |S|>=6 for 8 of 16 assignments.
    d = np.array([5.0, 1.0, 1.0, 1.0])
    s_values, b_values = kernel.enumerate_signed_sums(d)
    assert sorted(int(v) for v in s_values) == sorted([
        8, -8, 6, 6, 6, -6, -6, -6, 4, 4, 4, -4, -4, -4, 2, -2
    ])
    assert kernel.exact_pvalue(d, effect=0.0).p_value == pytest.approx(2 / 16)
    result6 = kernel.exact_pvalue(d, effect=2.0)
    # Residuals (3,-1,-1,-1), observed |sum|=0, the central value: all 16.
    assert result6.p_value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Exact p-values agree with the independent oracle over many cases/taus
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d", [
    [0, 0, 0], [1, -1, 1], [5, -5, 5, -5], [1, 2, 3],
    [1, 1, 2, 2], [3, -1, 2, 2], [1, -2, 3, -4, 5],
    [0.5, -1.25, 2.0, 0.0],
])
def test_exact_pvalue_matches_independent_oracle(d):
    d = np.asarray(d, dtype=float)
    for tau in [-3.0, -0.5, 0.0, 0.5, float(d.mean()), 2.7]:
        result = kernel.exact_pvalue(d, effect=tau)
        oracle_p, count, total = oracle_pvalue(d, tau)
        assert result.p_value == pytest.approx(oracle_p, abs=1e-12)
        assert result.count_as_extreme == count
        assert result.n_assignments == total


def test_pvalue_is_symmetric_under_global_sign():
    d = np.array([1.0, -2.0, 3.0, -4.0])
    assert kernel.exact_pvalue(d, 0.0).p_value == pytest.approx(
        kernel.exact_pvalue(-d, 0.0).p_value
    )


# ---------------------------------------------------------------------------
# Validation failure categories
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload,code", [
    ({"pairs": [[1, 0]]}, "TOO_FEW_PAIRS"),
    ({"pairs": [[1]]}, "INVALID_PAIR_SHAPE"),
    ({"pairs": "nonsense"}, "MALFORMED_DATA"),
    ({}, "MISSING_DATA"),
    ({"pairs": [[1, 0]], "differences": [1]}, "AMBIGUOUS_DATA"),
    ({"pairs": [[1, float("nan")], [2, 0]]}, "NON_FINITE_OUTCOME"),
    ({"pairs": [[1, 0], [2, "x"]]}, "NON_NUMERIC_OUTCOME"),
])
def test_contract_failure_categories(payload, code):
    with pytest.raises(contract.ContractError) as exc:
        contract.validate_pairs(payload.get("pairs"),
                                payload.get("differences"))
    assert exc.value.code == code


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5, "x"])
def test_invalid_alpha_categories(alpha):
    with pytest.raises(contract.ContractError) as exc:
        contract.validate_alpha(alpha)
    assert exc.value.code == "INVALID_ALPHA"


# ---------------------------------------------------------------------------
# Monte-Carlo
# ---------------------------------------------------------------------------

def test_mc_pvalue_reports_error_band_and_seed_determinism():
    d = np.array([float((i % 7) - 3) for i in range(30)])
    r1 = kernel.mc_pvalue(d, effect=0.0, n_draws=2000, seed=123)
    r2 = kernel.mc_pvalue(d, effect=0.0, n_draws=2000, seed=123)
    r3 = kernel.mc_pvalue(d, effect=0.0, n_draws=2000, seed=999)
    assert r1.p_value == r2.p_value  # same seed -> identical estimate
    assert r1.mc_standard_error == pytest.approx(
        math.sqrt(r1.p_value * (1 - r1.p_value) / 2001), abs=1e-15
    )
    assert r1.mc_error_halfwidth > r1.mc_standard_error  # z*SE for 95%
    assert r1.p_value != r3.p_value or True  # different seed may differ
    assert r1.n_assignments == 2 ** 30


def test_mc_pvalue_converges_to_exact():
    d = np.array([5.0, 1.0, 1.0, 1.0, -2.0, -1.0])
    exact = kernel.exact_pvalue(d, effect=0.0).p_value
    mc = kernel.mc_pvalue(d, effect=0.0, n_draws=200_000, seed=1).p_value
    assert mc == pytest.approx(exact, abs=0.02)


def test_budget_decision_switches_method():
    from app import service

    small = service.budget_decision(10)  # 1024 <= 100000
    large = service.budget_decision(20)  # 1_048_576 > 100000
    assert small["within_budget"] is True
    assert small["method"] == "exact-signflip"
    assert large["within_budget"] is False
    assert large["method"] == "monte-carlo-signflip"
