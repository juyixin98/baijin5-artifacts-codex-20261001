"""Exhaustive cross-validation of the Nussinov core against the oracle.

For every short sequence over a small alphabet the production DP optimum
must equal the brute-force optimum, and the production enumeration must
return exactly the oracle's set of optimal structures (no extras, none
missing, all independently verified legal).
"""
from __future__ import annotations

import itertools

import pytest

from nussinov_backend.domain import (
    MIN_LOOP_LENGTH,
    build_structure,
    check_structure,
    enumerate_optimal,
    fill_dp,
    fold,
    traceback_one,
)
from nussinov_backend.domain.verification import scalar_optimum
from tests.reference.brute_force import (
    all_legal_structures,
    is_nested_and_legal,
    optimal_structures,
    optimum_count,
    parse_dot_bracket,
)

# A small alphabet keeps exhaustive runs fast while exercising GU/GC/AU.
ALPHABET = "ACGU"


def _all_sequences(length: int):
    yield from ("".join(chars) for chars in itertools.product(ALPHABET, repeat=length))


@pytest.mark.parametrize("length", range(0, 9))
def test_dp_optimum_matches_brute_force_for_every_sequence(length: int):
    for sequence in _all_sequences(length):
        table = fill_dp(sequence)
        assert table.optimum == optimum_count(sequence), sequence
        # Independent plain-Python recurrence inside the package must agree.
        assert scalar_optimum(sequence) == table.optimum, sequence


@pytest.mark.parametrize("length", range(0, 9))
def test_enumeration_returns_exactly_all_optimal_structures(length: int):
    for sequence in _all_sequences(length):
        table = fill_dp(sequence)
        produced, truncated = enumerate_optimal(table, limit=10_000)
        assert not truncated, sequence
        produced_sets = {frozenset(s.pairs) for s in produced}
        expected = optimal_structures(sequence)
        assert produced_sets == expected, sequence
        for structure in produced:
            assert len(structure.pairs) == table.optimum
            assert is_nested_and_legal(sequence, structure.pairs)


@pytest.mark.parametrize("length", range(0, 9))
def test_traceback_primary_is_one_of_the_oracle_optima(length: int):
    for sequence in _all_sequences(length):
        table = fill_dp(sequence)
        primary = traceback_one(table)
        assert frozenset(primary.pairs) in optimal_structures(sequence), sequence


@pytest.mark.parametrize("length", range(1, 8))
def test_enumeration_truncation_flag_and_cap(length: int):
    for sequence in _all_sequences(length):
        table = fill_dp(sequence)
        produced, truncated = enumerate_optimal(table, limit=2)
        total = len(optimal_structures(sequence))
        assert len(produced) == min(2, total)
        assert truncated is (total > 2)


def test_concrete_sequence_gggaucc_detailed_result():
    # Hand-verified fixture: G G G A U C C (7 nt), positions 0..6.
    # Distance must be >= 4 (3 enclosed bases). Admissible pairs:
    #   (0,4) GU, (0,5) GC, (0,6) GC, (1,5) GC, (1,6) GC, (2,6) GC.
    # Two pairs coexist only as the nested stack (0,6)+(1,5): the inner
    # pair encloses exactly 3 bases (positions 2,3,4). This is the UNIQUE
    # optimum; (2,4) GU is illegal (only 1 base enclosed).
    sequence = "GGGAUCC"
    result = fold(sequence, enumerate_alternatives=True, alternatives_limit=50)
    assert result.optimum == 2
    expected_optima = optimal_structures(sequence)
    assert expected_optima == {frozenset({(0, 6), (1, 5)})}
    got = {frozenset(s.pairs) for s in (result.primary,) + result.alternatives}
    assert got == expected_optima
    assert result.primary.dot_bracket == "((...))"
    assert result.primary.pair_table == (7, 6, 0, 0, 0, 2, 1)


def test_no_pairable_bases_gives_empty_pairing():
    sequence = "AAAAAAAA"
    result = fold(sequence)
    assert result.optimum == 0
    assert result.primary.pairs == ()
    assert result.primary.dot_bracket == "." * 8
    assert result.primary.pair_table == (0,) * 8
    assert result.self_check.valid


def test_short_sequences_cannot_pair_under_min_loop():
    # Every sequence of length <= min_loop+1 = 4 must have optimum 0.
    for length in range(1, 5):
        for sequence in _all_sequences(length):
            assert fill_dp(sequence).optimum == 0


def test_min_loop_boundary_exact_three_enclosed_bases():
    # j - i - 1 == 3  -> legal; == 2 -> illegal.
    legal = fill_dp("GAAAC")
    assert legal.optimum == 1
    illegal = fill_dp("GAAC")
    assert illegal.optimum == 0
    # Check the DP cell directly: only the boundary cell flips.
    assert legal.value(0, 4) == 1
    assert illegal.value(0, 3) == 0


def test_dot_bracket_pair_table_and_pairs_are_consistent():
    sequence = "GGGAAACCC"
    result = fold(sequence)
    structure = result.primary
    assert structure.pair_count == 3
    assert structure.dot_bracket == "(((...)))"
    assert structure.pairs == ((0, 8), (1, 7), (2, 6))
    # Every bracket pair parses back to the pair list.
    assert parse_dot_bracket(structure.dot_bracket) == set(structure.pairs)
    # The 1-based table is the inverse mapping of the pair list.
    for i, j in structure.pairs:
        assert structure.pair_table[i] == j + 1
        assert structure.pair_table[j] == i + 1
    assert sum(1 for v in structure.pair_table if v) == 2 * len(structure.pairs)


def test_multiple_optima_primary_is_deterministic():
    # GGAUCC has 3 distinct optima; traceback must pick the same one forever.
    table = fill_dp("GGAUCC")
    first = traceback_one(table).dot_bracket
    for _ in range(20):
        assert traceback_one(fill_dp("GGAUCC")).dot_bracket == first
    # Deterministic rule leaves base 0 unpaired when optimal -> (1,5) 1-based.
    assert first == ".(...)"


def test_enumeration_first_element_equals_primary():
    table = fill_dp("GGAUCC")
    primary = traceback_one(table)
    enumerated, _ = enumerate_optimal(table, limit=10)
    assert enumerated[0].pairs == primary.pairs


def test_check_structure_flags_each_violation_category():
    sequence = "GGAUCC"
    # Non-canonical pair.
    check = check_structure(sequence, ((0, 1),))
    codes = {v.code for v in check.violations}
    assert "loop_too_short" in codes and "non_canonical_pair" in codes
    # Crossing pairs (pseudoknot) flagged even though both are G-C pairs.
    crossing = check_structure("GGCGAUCC", ((0, 5), (1, 6)))
    assert "pseudoknot_crossing" in {v.code for v in crossing.violations}
    # Base used twice.
    doubled = check_structure("GGGAAACCC", ((0, 8), (0, 7)))
    assert "base_used_twice" in {v.code for v in doubled.violations}


def test_pseudoknot_absent_from_all_oracle_outputs():
    # Exhaustively: nothing the oracle enumerates contains a crossing.
    for sequence in _all_sequences(8):
        for structure in all_legal_structures(sequence):
            assert is_nested_and_legal(sequence, structure)


def test_build_structure_sorts_pairs_and_renders_consistently():
    structure = build_structure("GGAUCC", ((4, 0),))
    assert structure.pairs == ((0, 4),)
    assert structure.dot_bracket == "(...)."
    assert structure.pair_table == (5, 0, 0, 0, 1, 0)
