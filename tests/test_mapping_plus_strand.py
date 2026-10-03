"""Plus-strand point mapping against hand-computed answers."""

from __future__ import annotations

from txmap.models import Reason, Status

from . import reference_answers as ref


def test_plus_strand_genomic_to_transcript_exact_positions(mapper):
    for gpos, expected_tpos in ref.TXA_G2T_OK.items():
        out = mapper.genomic_to_transcript("txA", gpos)
        assert out.status is Status.OK, f"g={gpos}: {out}"
        assert out.mapped == expected_tpos, f"g={gpos}: got {out.mapped}"


def test_plus_strand_transcript_to_genomic_exact_positions(mapper):
    for tpos, expected_gpos in ref.TXA_T2G_OK.items():
        out = mapper.transcript_to_genomic("txA", tpos)
        assert out.status is Status.OK, f"t={tpos}: {out}"
        assert out.mapped == expected_gpos, f"t={tpos}: got {out.mapped}"


def test_intronic_positions_are_rejected_not_hard_mapped(mapper):
    for gpos in ref.TXA_G2T_INTRONIC:
        out = mapper.genomic_to_transcript("txA", gpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.INTRONIC
        assert out.mapped is None


def test_adjacent_exon_boundaries(mapper):
    """Half-open semantics: exon end is excluded, next exon start included.

    g=19 is the last base of exon 1 (t=9); g=20 is the first intronic base;
    t=9 and t=10 are adjacent in the transcript but jump the intron on the
    genome (19 -> 30).
    """
    assert mapper.genomic_to_transcript("txA", 19).mapped == 9
    intronic = mapper.genomic_to_transcript("txA", 20)
    assert intronic.status is Status.REJECTED
    assert intronic.reason is Reason.INTRONIC
    assert mapper.transcript_to_genomic("txA", 9).mapped == 19
    assert mapper.transcript_to_genomic("txA", 10).mapped == 30


def test_out_of_transcript_positions(mapper):
    for gpos in ref.TXA_G2T_OUT_OF_TRANSCRIPT:
        out = mapper.genomic_to_transcript("txA", gpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.OUT_OF_TRANSCRIPT, f"g={gpos}: {out.reason}"


def test_out_of_contig_positions(mapper):
    for gpos in ref.TXA_G2T_OUT_OF_CONTIG:
        out = mapper.genomic_to_transcript("txA", gpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.OUT_OF_CONTIG


def test_transcript_coordinate_past_end_rejected(mapper):
    for tpos in ref.TXA_T2G_OUT_OF_TRANSCRIPT:
        out = mapper.transcript_to_genomic("txA", tpos)
        assert out.status is Status.REJECTED
        assert out.reason is Reason.OUT_OF_TRANSCRIPT


def test_negative_position_rejected(mapper):
    out = mapper.genomic_to_transcript("txA", -1)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INVALID_POSITION
    out = mapper.transcript_to_genomic("txA", -1)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INVALID_POSITION
