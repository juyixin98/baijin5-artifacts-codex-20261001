"""Minus-strand mapping: order reversal and base direction."""

from __future__ import annotations

from txmap.models import Reason, Status

from . import reference_answers as ref


def test_minus_strand_genomic_to_transcript_exact_positions(mapper):
    for gpos, expected_tpos in ref.TXB_G2T_OK.items():
        out = mapper.genomic_to_transcript("txB", gpos)
        assert out.status is Status.OK, f"g={gpos}: {out}"
        assert out.mapped == expected_tpos, f"g={gpos}: got {out.mapped}"


def test_minus_strand_transcript_to_genomic_exact_positions(mapper):
    for tpos, expected_gpos in ref.TXB_T2G_OK.items():
        out = mapper.transcript_to_genomic("txB", tpos)
        assert out.status is Status.OK, f"t={tpos}: {out}"
        assert out.mapped == expected_gpos, f"t={tpos}: got {out.mapped}"


def test_minus_strand_direction_is_reversed(mapper):
    """On the minus strand, increasing transcript position means DECREASING
    genomic position within an exon."""
    g_at_0 = mapper.transcript_to_genomic("txB", 0).mapped
    g_at_1 = mapper.transcript_to_genomic("txB", 1).mapped
    assert g_at_0 == 99 and g_at_1 == 98
    assert g_at_1 < g_at_0


def test_minus_strand_introns_and_outside(mapper):
    for gpos in ref.TXB_G2T_INTRONIC:
        out = mapper.genomic_to_transcript("txB", gpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.INTRONIC
    for gpos in ref.TXB_G2T_OUT_OF_TRANSCRIPT:
        out = mapper.genomic_to_transcript("txB", gpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.OUT_OF_TRANSCRIPT


def test_minus_strand_transcript_past_end(mapper):
    for tpos in ref.TXB_T2G_OUT_OF_TRANSCRIPT:
        out = mapper.transcript_to_genomic("txB", tpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.OUT_OF_TRANSCRIPT


def test_spliced_sequences_match_hand_derived_strings(mapper):
    status, _, seq = mapper.spliced_sequence("txA")
    assert status is Status.OK
    assert seq == ref.TXA_SPLICED
    status, _, seq = mapper.spliced_sequence("txB")
    assert status is Status.OK
    assert seq == ref.TXB_SPLICED
    status, _, seq = mapper.spliced_sequence("txC")
    assert status is Status.OK
    assert seq == ref.TXC_SPLICED


def test_base_direction_at_mapped_positions(mapper):
    """The base at a transcript position must equal the (possibly
    complemented) reference base at the mapped genomic position."""
    for tx_id, tpos, expected_base in ref.BASE_CHECKS:
        status, _, spliced = mapper.spliced_sequence(tx_id)
        assert status is Status.OK
        assert spliced[tpos] == expected_base, (
            f"{tx_id} t={tpos}: expected {expected_base}, got {spliced[tpos]}"
        )
