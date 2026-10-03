"""Unit tests for the consensus coverage floor.

The critical safety property: a column with too little effective coverage
must NEVER be reported as conserved, even if 100% of the (scarce)
observations agree.
"""

import pytest
from conftest import judgement, read_fixture

from msa_backend.config import load_config
from msa_backend.domain.consensus import call_consensus
from msa_backend.parsing.fasta import parse_fasta_alignment
from msa_backend.pipeline import compute_alignment


def test_skewed_distribution_with_low_coverage_is_not_conserved():
    consensus, status = call_consensus(
        distribution={"A": 1.0, "C": 0.0, "G": 0.0, "T": 0.0},
        effective_coverage=1.0,  # below the 2.0 floor
        conservation_threshold=0.6,
        min_effective_coverage=2.0,
    )
    judgement(
        "synthetic-column", "coverage-floor",
        "100% A but coverage 1.0 < 2.0 -> insufficient_coverage, never conserved",
    )
    assert status == "insufficient_coverage"
    assert consensus == "?"


def test_coverage_exactly_at_floor_allows_conserved_call():
    consensus, status = call_consensus(
        distribution={"A": 1.0, "C": 0.0, "G": 0.0, "T": 0.0},
        effective_coverage=2.0,
        conservation_threshold=0.6,
        min_effective_coverage=2.0,
    )
    judgement("synthetic-column", "coverage-floor", "coverage == floor is sufficient")
    assert status == "conserved"
    assert consensus == "A"


def test_zero_coverage_column_is_insufficient():
    consensus, status = call_consensus(
        distribution={"A": 0.0, "C": 0.0, "G": 0.0, "T": 0.0},
        effective_coverage=0.0,
        conservation_threshold=0.6,
        min_effective_coverage=2.0,
    )
    judgement("synthetic-column", "coverage-floor", "all-gap column -> insufficient_coverage")
    assert status == "insufficient_coverage"
    assert consensus == "?"


def test_sufficient_coverage_without_dominant_base_is_variable():
    consensus, status = call_consensus(
        distribution={"A": 0.5, "C": 0.0, "G": 0.0, "T": 0.5},
        effective_coverage=4.0,
        conservation_threshold=0.6,
        min_effective_coverage=2.0,
    )
    judgement("synthetic-column", "consensus", "no base >= 0.6 -> variable, lowercase symbol")
    assert status == "variable"
    assert consensus == "a"


def test_gappy_fixture_low_coverage_columns_are_not_conserved():
    config = load_config()
    alignment = parse_fasta_alignment(
        read_fixture("gappy.fa"), config.algorithm.gap_symbol
    )
    result = compute_alignment(alignment, config)
    judgement(
        "gappy.fa", "coverage-floor",
        "columns 3-4 have coverage 1.0 (< 2.0) despite 100% residue agreement",
    )
    for column in (result.columns[2], result.columns[3]):
        assert column.effective_coverage == pytest.approx(1.0)
        assert column.status == "insufficient_coverage"
        assert column.consensus == "?"
        # the distribution itself is fully skewed -- the floor, not the
        # distribution, drives the call
        assert max(column.distribution.values()) == pytest.approx(1.0)
