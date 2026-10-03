"""Sanity tests for the independent brute-force oracle itself.

The oracle is the reference answer source; these tests pin its behavior on
hand-verified cases so it cannot silently agree with the production code by
being wrong in the same way.
"""
from __future__ import annotations

from tests.reference.brute_force import (
    all_legal_structures,
    is_nested_and_legal,
    optimal_structures,
    optimum_count,
    pair_count_distribution,
    parse_dot_bracket,
)


def test_oracle_empty_and_single_base():
    assert all_legal_structures("") == {frozenset()}
    assert optimum_count("") == 0
    assert all_legal_structures("A") == {frozenset()}
    assert optimum_count("A") == 0


def test_oracle_min_loop_boundary_is_enforced():
    # Two compatible bases adjacent: loop length 0 < 3, pairing illegal.
    assert optimum_count("GC") == 0
    assert all_legal_structures("GC") == {frozenset()}
    # Exactly three bases enclosed -> the smallest legal hairpin.
    assert optimum_count("GAAAC") == 1
    assert optimal_structures("GAAAC") == {frozenset({(0, 4)})}
    # Two enclosed bases -> still illegal under min loop 3.
    assert optimum_count("GAAC") == 0


def test_oracle_wobble_pair_gu_is_allowed():
    assert optimal_structures("GAAAU") == {frozenset({(0, 4)})}
    # Reverse orientation U-G is equally valid.
    assert optimal_structures("UAAAG") == {frozenset({(0, 4)})}


def test_oracle_three_optimal_one_pair_structures():
    # GGAUCC, hand-verified: exactly three legal 1-pair structures,
    # no 2-pair non-crossing structure exists (the nested candidate (2,6)
    # 1-based encloses only 2 bases and is illegal).
    found = optimal_structures("GGAUCC")
    assert found == {
        frozenset({(0, 4)}),
        frozenset({(0, 5)}),
        frozenset({(1, 5)}),
    }
    assert optimum_count("GGAUCC") == 1
    assert pair_count_distribution("GGAUCC") == {0: 1, 1: 3}


def test_oracle_counts_all_noncrossing_for_all_unpaired():
    # No compatible bases at all -> exactly one structure (the empty one).
    assert all_legal_structures("AAAAAA") == {frozenset()}


def test_oracle_nested_three_pair_hairpin():
    # GGGAAACCC: three nested G-C pairs (1,9)(2,8)(3,7), each loop >= 3.
    found = optimal_structures("GGGAAACCC")
    assert found == {frozenset({(0, 8), (1, 7), (2, 6)})}
    assert optimum_count("GGGAAACCC") == 3


def test_oracle_every_enumerated_structure_is_independently_legal():
    sequence = "GGCGAUCCGA"
    for structure in all_legal_structures(sequence):
        assert is_nested_and_legal(sequence, structure)


def test_oracle_rejects_crossing_pairs_as_not_nested():
    # (1,4) and (2,5) cross: a<c<b<d.
    assert not is_nested_and_legal("GCAUGC", {(0, 3), (1, 4)})
    # Same pairs nested/disjoint are fine when bases allow.
    assert is_nested_and_legal("GGAAACCU", {(0, 7), (1, 6)})


def test_parse_dot_bracket_reference():
    assert parse_dot_bracket("((...))") == {(0, 6), (1, 5)}
    assert parse_dot_bracket("..()..") == {(2, 3)}  # note: illegal as RNA, parser is generic
    assert parse_dot_bracket("......") == set()
