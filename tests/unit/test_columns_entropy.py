"""Unit tests comparing the core implementation against hand-computed
reference answers (static JSON) AND the independent stdlib calculator.

Nothing here accepts "the endpoint ran" as success: every column's
distribution, entropy, information content, coverage, gap fraction,
consensus symbol and status are asserted against pre-computed values.
"""

import math

import pytest
from conftest import judgement, load_expected, read_fixture

import independent_calc

from msa_backend.config import load_config
from msa_backend.parsing.fasta import parse_fasta_alignment
from msa_backend.pipeline import compute_alignment

TOL = 1e-9
ALL_FIXTURES = ["conserved", "diverse", "gappy", "duplicates", "duplicates_pair", "ambiguous"]


def _compute(fixture_name: str):
    config = load_config()
    text = read_fixture(f"{fixture_name}.fa")
    alignment = parse_fasta_alignment(text, config.algorithm.gap_symbol)
    return compute_alignment(alignment, config)


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_columns_match_static_reference(fixture_name):
    expected = load_expected(fixture_name)
    result = _compute(fixture_name)
    judgement(
        f"{fixture_name}.fa", "columns-vs-static-reference",
        f"expected values from tests/reference/expected_{fixture_name}.json "
        f"(derivation: {expected['derivation']})",
    )
    assert len(result.columns) == len(expected["columns"])
    for got, want in zip(result.columns, expected["columns"]):
        assert got.column_index == want["column_index"]
        for base, freq in want["distribution"].items():
            assert got.distribution[base] == pytest.approx(freq, abs=TOL), (
                f"{fixture_name} col{got.column_index} base {base}"
            )
        assert got.entropy_bits == pytest.approx(want["entropy_bits"], abs=TOL)
        assert got.information_content_bits == pytest.approx(
            want["information_content_bits"], abs=TOL
        )
        assert got.effective_coverage == pytest.approx(want["effective_coverage"], abs=TOL)
        assert got.gap_fraction == pytest.approx(want["gap_fraction"], abs=TOL)
        assert got.consensus == want["consensus"]
        assert got.status == want["status"]


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_columns_match_independent_calculator(fixture_name):
    independent = independent_calc.expected_columns(f"data/fixtures/{fixture_name}.fa")
    result = _compute(fixture_name)
    judgement(
        f"{fixture_name}.fa", "columns-vs-independent-calc",
        "cross-check against tests/reference/independent_calc.py (stdlib-only)",
    )
    for got, want in zip(result.columns, independent):
        assert got.entropy_bits == pytest.approx(want["entropy_bits"], abs=TOL)
        assert got.consensus == want["consensus"]
        assert got.status == want["status"]


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_weights_match_static_reference(fixture_name):
    expected = load_expected(fixture_name)
    result = _compute(fixture_name)
    judgement(
        f"{fixture_name}.fa", "weights-vs-static-reference",
        "weights and total weight from static reference JSON",
    )
    for seq_id, want in expected["weights"].items():
        assert result.weights[seq_id] == pytest.approx(want, abs=TOL)
    assert result.total_weight == pytest.approx(expected["total_weight"], abs=TOL)


def test_conserved_columns_have_zero_entropy_and_full_information():
    result = _compute("conserved")
    judgement(
        "conserved.fa", "entropy",
        "columns 1-10 identical across all sequences -> H=0 bits, IC=2 bits",
    )
    for column in result.columns[:10]:
        assert column.entropy_bits == pytest.approx(0.0, abs=TOL)
        assert column.information_content_bits == pytest.approx(2.0, abs=TOL)
        assert column.status == "conserved"


def test_diverse_columns_have_max_entropy_and_zero_information():
    result = _compute("diverse")
    judgement(
        "diverse.fa", "entropy",
        "each column is a uniform A/C/G/T mix -> H=log2(4)=2 bits, IC=0",
    )
    for column in result.columns:
        assert column.entropy_bits == pytest.approx(2.0, abs=TOL)
        assert column.information_content_bits == pytest.approx(0.0, abs=TOL)
        assert column.status == "variable"


def test_entropy_matches_hand_computed_log2_3():
    """gappy col8: T/A/C each carry weight 1.0 of 3.0 -> H = log2(3)."""
    result = _compute("gappy")
    column = result.columns[7]
    judgement(
        "gappy.fa col8", "entropy",
        "hand computation: three bases at p=1/3 each -> H=log2(3)=1.584962...",
    )
    assert column.entropy_bits == pytest.approx(math.log2(3), abs=TOL)
    assert column.information_content_bits == pytest.approx(2.0 - math.log2(3), abs=TOL)


def test_duplicate_copies_do_not_amplify_evidence():
    """duplicates.fa col8 must equal duplicates_pair.fa col8 exactly, and
    must differ from the naive unweighted answer (T 3/4, A 1/4)."""
    dup = _compute("duplicates").columns[7]
    pair = _compute("duplicates_pair").columns[7]
    naive_unweighted_entropy = -(0.75 * math.log2(0.75) + 0.25 * math.log2(0.25))
    judgement(
        "duplicates.fa col8", "weighting",
        "weighted result must equal the 2-sequence pair (H=1.0), not the "
        f"unweighted 4-row answer (H={naive_unweighted_entropy:.6f})",
    )
    assert dup.entropy_bits == pytest.approx(pair.entropy_bits, abs=TOL)
    assert dup.entropy_bits == pytest.approx(1.0, abs=TOL)
    assert abs(dup.entropy_bits - naive_unweighted_entropy) > 0.1
    assert dup.distribution["T"] == pytest.approx(0.5, abs=TOL)
    assert dup.distribution["A"] == pytest.approx(0.5, abs=TOL)


def test_ambiguous_symbols_split_uniformly():
    """ambiguous.fa col2: C(0.5) + N(1.0 -> 0.25 each) + R(1.0 -> A/G 0.5)
    + C(0.5), total weight 3.0 -> A 0.25, C 5/12, G 0.25, T 1/12."""
    result = _compute("ambiguous")
    column = result.columns[1]
    judgement(
        "ambiguous.fa col2", "ambiguity-policy",
        "fixed uniform_split: N spreads 1/4 to each base, R spreads 1/2 to A and G",
    )
    assert column.distribution["A"] == pytest.approx(0.25, abs=TOL)
    assert column.distribution["C"] == pytest.approx(5 / 12, abs=TOL)
    assert column.distribution["G"] == pytest.approx(0.25, abs=TOL)
    assert column.distribution["T"] == pytest.approx(1 / 12, abs=TOL)
    assert sum(column.distribution.values()) == pytest.approx(1.0, abs=TOL)


def test_gap_handling_independent_of_weight_total():
    """gappy col2: one gap among weight-3.0 total; distribution is
    normalised by the non-gap weight (2.0) only."""
    result = _compute("gappy")
    column = result.columns[1]
    judgement(
        "gappy.fa col2", "gap-handling",
        "C frequency 1.0 over non-gap weight 2.0; gap_fraction 1/3 reported separately",
    )
    assert column.effective_coverage == pytest.approx(2.0, abs=TOL)
    assert column.distribution["C"] == pytest.approx(1.0, abs=TOL)
    assert column.gap_fraction == pytest.approx(1.0 / 3.0, abs=TOL)
