"""Round-trip identity, transcript identity isolation, unknown transcripts."""

from __future__ import annotations

from txmap.models import Reason, Status

from . import reference_answers as ref


def test_roundtrip_all_exonic_genomic_positions(mapper):
    """Every exonic genomic position survives g -> t -> g identically."""
    for tx_id in mapper.transcript_ids():
        tx = mapper.get_transcript(tx_id)
        assert tx is not None
        for exon in tx.exons:
            for gpos in range(exon.start, exon.end):
                fwd = mapper.genomic_to_transcript(tx_id, gpos)
                assert fwd.status is Status.OK, f"{tx_id} g={gpos}"
                back = mapper.transcript_to_genomic(tx_id, fwd.mapped)
                assert back.status is Status.OK
                assert back.mapped == gpos, f"{tx_id} g={gpos} t={fwd.mapped}"


def test_roundtrip_all_transcript_positions(mapper):
    """Every transcript position survives t -> g -> t identically."""
    for tx_id in mapper.transcript_ids():
        tx = mapper.get_transcript(tx_id)
        assert tx is not None
        for tpos in range(tx.tx_length):
            fwd = mapper.transcript_to_genomic(tx_id, tpos)
            assert fwd.status is Status.OK, f"{tx_id} t={tpos}"
            back = mapper.genomic_to_transcript(tx_id, fwd.mapped)
            assert back.status is Status.OK
            assert back.mapped == tpos, f"{tx_id} t={tpos} g={fwd.mapped}"


def test_transcript_identity_isolation(mapper):
    """Same genomic coordinate, different transcripts: independent results."""
    for tx_id, gpos, exp_status, exp_reason, exp_t in ref.ISOLATION_CASES:
        out = mapper.genomic_to_transcript(tx_id, gpos)
        assert out.status.value == exp_status, f"{tx_id} g={gpos}: {out.status}"
        assert out.reason.value == exp_reason
        assert out.mapped == exp_t


def test_unknown_transcript_rejected(mapper):
    out = mapper.genomic_to_transcript(ref.UNKNOWN_TRANSCRIPT_ID, 50)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.UNKNOWN_TRANSCRIPT
    out = mapper.transcript_to_genomic(ref.UNKNOWN_TRANSCRIPT_ID, 0)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.UNKNOWN_TRANSCRIPT
    out = mapper.genomic_interval_to_transcript(ref.UNKNOWN_TRANSCRIPT_ID, 0, 10)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.UNKNOWN_TRANSCRIPT
    status, reason, seq = mapper.spliced_sequence(ref.UNKNOWN_TRANSCRIPT_ID)
    assert status is Status.REJECTED
    assert reason is Reason.UNKNOWN_TRANSCRIPT
    assert seq is None


def test_indeterminate_when_contig_missing():
    """A transcript whose contig is absent from the reference cannot be
    decided: INDETERMINATE, not a wrong answer."""
    from txmap.mapping import Mapper
    from txmap.models import Exon, Strand, Transcript

    orphan = Transcript(
        tx_id="txOrphan",
        gene="geneX",
        contig="chrMissing",
        strand=Strand.PLUS,
        exons=(Exon(0, 10),),
    )
    m = Mapper({"txOrphan": orphan}, contigs={})
    out = m.genomic_to_transcript("txOrphan", 5)
    assert out.status is Status.INDETERMINATE
    assert out.reason is Reason.UNKNOWN_CONTIG
    status, reason, seq = m.spliced_sequence("txOrphan")
    assert status is Status.INDETERMINATE
    assert reason is Reason.SEQUENCE_UNAVAILABLE
