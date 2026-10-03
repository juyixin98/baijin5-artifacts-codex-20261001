"""Strand-aware sequence tests (hand-computed synthetic bases)."""

from __future__ import annotations

import pytest

from txmap.sequence import (
    bases_at_genomic_positions,
    bases_at_transcript_positions,
    complement,
    genomic_subsequence,
    reverse_complement,
    transcript_sequence,
)

pytestmark = pytest.mark.unit


def test_complement_and_reverse_complement_basic():
    assert complement("ACGT") == "TGCA"
    assert reverse_complement("ACGT") == "ACGT"
    assert reverse_complement("AAGG") == "CCTT"


def test_genomic_subsequence_is_pattern_periodic():
    # pattern ACGT: g=100 -> 100 % 4 = 0 -> A; g=101 -> C
    assert genomic_subsequence("ACGT", 100, 108) == "ACGTACGT"
    assert genomic_subsequence("ACGT", 101, 105) == "CGTA"


def test_genomic_subsequence_rejects_bad_interval():
    with pytest.raises(ValueError):
        genomic_subsequence("ACGT", 10, 9)
    with pytest.raises(ValueError):
        genomic_subsequence("ACGT", -1, 3)


def test_plus_transcript_sequence_hand_checked(t1):
    # E0[100,130): 100%4=0 -> starts A, 30 nt = 7.5 repeats
    seq = transcript_sequence(t1, "ACGT")
    assert len(seq) == 80
    assert seq[:4] == "ACGT"
    assert seq[29] == "ACGT"[129 % 4]  # last base of E0
    # first base of E1 at tx 30: g=160, 160%4=0 -> A
    assert seq[30] == "A"  # g=160, 160%4=0 -> A
    # first base of E2 at tx 50: g=210, 210%4=2 -> pattern[2] = G
    assert seq[50] == "G"


def test_minus_transcript_sequence_orientation(t2):
    seq = transcript_sequence(t2, "ACGT")
    assert len(seq) == 75
    # tx base 0 corresponds to genomic 639 on the opposite strand:
    # ref(639) = pattern[639 % 4] = pattern[3] = T; complement = A
    assert seq[0] == "A"
    # tx base 20 corresponds to genomic 584: 584 % 4 = 0 -> ref A, comp T
    assert seq[20] == "T"
    # tx base 74 corresponds to genomic 500: 500%4=0 -> A, comp T
    assert seq[74] == "T"


def test_minus_bases_match_point_mapping(t2, m2, patterns):
    pattern = patterns[t2.chrom]
    seq = transcript_sequence(t2, pattern)
    tx_positions = [0, 19, 20, 44, 45, 74]
    tx_bases = bases_at_transcript_positions(t2, pattern, tx_positions)
    genomic_positions = [m2.tx_to_genomic_point(p).genomic_position for p in tx_positions]
    ref_bases = bases_at_genomic_positions(pattern, genomic_positions)
    # each transcript base must be the complement of the mapped reference base
    for tb, rb in zip(tx_bases, ref_bases):
        assert tb == complement(rb)
    assert tx_bases == seq[0] + seq[19] + seq[20] + seq[44] + seq[45] + seq[74]


def test_minus_sequence_crosschecked_against_independent_oracle(t2, patterns):
    from oracle import build_point_maps, tx_base

    pattern = patterns[t2.chrom]
    exons = [(e.start, e.end) for e in t2.exons]
    tx2g, _ = build_point_maps(exons, "-")
    seq = transcript_sequence(t2, pattern)
    # every mature transcript base must equal the oracle's strand-aware base
    assert all(seq[p] == tx_base("-", pattern, g) for p, g in enumerate(tx2g))
