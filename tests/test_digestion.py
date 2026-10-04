"""Tests for digest enumeration: boundaries, empty pieces, missed cleavages,
positional identity preservation. Expected values are hand computed and
cross-checked against the independent oracle in tests/oracle.py."""

from __future__ import annotations

from app.domain.digestion import digest
from app.domain.rules import BUILTIN_ENZYMES

from tests.oracle import (
    oracle_all_fragments,
    oracle_neutral_mass,
    oracle_primary_fragments,
)

ENZYMES = {e.name: e for e in BUILTIN_ENZYMES}


def test_complete_digest_hand_computed_fragments_and_boundaries():
    # Trypsin (KR, block P) on AAKRPA:
    #   bond3 K|R CUT, bond4 R|P BLOCKED -> pieces AAK | RPA
    result = digest("AAKRPA", ENZYMES["trypsin_syn"], missed_cleavages=0)
    assert result.cut_bonds == (3,)
    assert result.blocked_bonds == (4,)
    assert result.primary_fragment_count == 2
    assert result.empty_fragments_suppressed == 0

    f1, f2 = result.fragments
    assert (f1.sequence, f1.start, f1.end, f1.length) == ("AAK", 1, 3, 3)
    assert (f2.sequence, f2.start, f2.end, f2.length) == ("RPA", 4, 6, 3)
    assert f1.is_nterminal and not f1.is_cterminal
    assert not f2.is_nterminal and f2.is_cterminal
    assert f1.n_term_offset == 0 and f1.c_term_offset == 3
    assert f2.n_term_offset == 3 and f2.c_term_offset == 6
    assert f1.fragment_id != f2.fragment_id


def test_consecutive_cut_sites_do_not_create_empty_fragments():
    # KKK|A: cuts at 1,2,3 -> K, K, K, A (no empty pieces).
    result = digest("KKKA", ENZYMES["trypsin_syn"], missed_cleavages=0)
    assert result.cut_bonds == (1, 2, 3)
    assert result.empty_fragments_suppressed == 0
    assert [(f.sequence, f.start, f.end) for f in result.fragments] == [
        ("K", 1, 1),
        ("K", 2, 2),
        ("K", 3, 3),
        ("A", 4, 4),
    ]


def test_identical_sequences_keep_distinct_positions_and_ids():
    result = digest("KKKA", ENZYMES["trypsin_syn"], missed_cleavages=0)
    ids = {f.fragment_id for f in result.fragments[:3]}
    starts = [f.start for f in result.fragments[:3]]
    assert len(ids) == 3
    assert starts == [1, 2, 3]


def test_single_residue_yields_exactly_one_fragment():
    result = digest("K", ENZYMES["trypsin_syn"], missed_cleavages=0)
    assert result.cut_bonds == ()
    assert len(result.fragments) == 1
    f = result.fragments[0]
    assert f.sequence == "K"
    assert f.is_nterminal and f.is_cterminal
    assert f.n_term_offset == 0 and f.c_term_offset == 1


def test_no_cut_sites_yields_whole_sequence_fragment():
    result = digest("AAAAA", ENZYMES["trypsin_syn"], missed_cleavages=0)
    assert len(result.fragments) == 1
    assert result.fragments[0].sequence == "AAAAA"
    assert result.fragments[0].start == 1
    assert result.fragments[0].end == 5


def test_missed_cleavage_enumeration_matches_independent_oracle():
    seq = "AAKFAKLA"
    for mc in range(4):
        result = digest(seq, ENZYMES["trypsin_no_proline_rule"], missed_cleavages=mc)
        cuts, expected = oracle_all_fragments(seq, "KR", "", mc)
        assert tuple(result.cut_bonds) == tuple(cuts)
        got = [
            (f.sequence, f.start, f.end, f.missed_cleavages)
            for f in result.fragments
        ]
        assert got == expected


def test_missed_cleavage_counts_and_spanned_bonds():
    # "AAKFAKLA": K at residues 3 and 6 -> cuts at bonds 3,6: AAK | FAK | LA
    result = digest("AAKFAKLA", ENZYMES["trypsin_no_proline_rule"],
                    missed_cleavages=2)
    by_seq = {(f.start, f.end): f for f in result.fragments}
    full = by_seq[(1, 8)]
    assert full.sequence == "AAKFAKLA"
    assert full.missed_cleavages == 2
    assert full.spanned_cut_bonds == (3, 6)
    first_union = by_seq[(1, 6)]
    assert first_union.sequence == "AAKFAK"
    assert first_union.missed_cleavages == 1
    assert first_union.spanned_cut_bonds == (3,)


def test_complete_digest_zero_missed_is_subset_of_higher_missed():
    r0 = digest("AAKFAKLA", ENZYMES["trypsin_no_proline_rule"], 0)
    r1 = digest("AAKFAKLA", ENZYMES["trypsin_no_proline_rule"], 1)
    zero_mc = [f for f in r1.fragments if f.missed_cleavages == 0]
    assert len(zero_mc) == len(r0.fragments)
    assert [(f.start, f.end) for f in zero_mc] == [
        (f.start, f.end) for f in r0.fragments
    ]


def test_fragment_sequences_tile_the_primary_digest():
    seq = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ"
    result = digest(seq, ENZYMES["trypsin_syn"], 0)
    rebuilt = "".join(f.sequence for f in result.fragments)
    assert rebuilt == seq
    # Adjacent fragments share boundaries exactly.
    for prev, nxt in zip(result.fragments, result.fragments[1:]):
        assert prev.c_term_offset == nxt.n_term_offset


def test_oracle_primary_fragments_agree_on_many_cases():
    cases = [
        "AAKRPA", "KKKA", "AKARPA", "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ",
        "KP" * 6, "RPRPKPKR", "AAAAAAAAAA",
    ]
    for seq in cases:
        result = digest(seq, ENZYMES["trypsin_syn"], 0)
        cuts, pieces = oracle_primary_fragments(seq, "KR", "P")
        assert tuple(result.cut_bonds) == tuple(cuts)
        assert [
            (f.sequence, f.start, f.end) for f in result.fragments
        ] == pieces


def test_mature_mass_matches_hand_sum_for_primary_fragments():
    result = digest("AAKFAKLA", ENZYMES["trypsin_no_proline_rule"], 0)
    for f in result.fragments:
        raw_neutral = oracle_neutral_mass(f.sequence)
        assert f.mass.status == "EXACT"
        assert f.mass.neutral_mass == round(raw_neutral, 6)
        # m/z must be rounded from the un-rounded neutral mass, not from the
        # already-rounded display value.
        assert f.mass.mhplus_mz == round(raw_neutral + 1.007276466621, 6)
