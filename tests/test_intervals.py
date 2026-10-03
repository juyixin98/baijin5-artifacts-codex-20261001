"""Interval mapping: multi-exon splitting, fragment order, length conservation."""

from __future__ import annotations

from txmap.models import Reason, Status

from . import reference_answers as ref


def _frag_dicts(outcome):
    return [
        {
            "g_start": f.g_start,
            "g_end": f.g_end,
            "t_start": f.t_start,
            "t_end": f.t_end,
        }
        for f in outcome.fragments
    ]


def _gap_dicts(outcome):
    return [
        {"g_start": g.g_start, "g_end": g.g_end, "reason": g.reason.value}
        for g in outcome.gaps
    ]


def test_plus_strand_genomic_interval_splits_across_intron(mapper):
    out = mapper.genomic_interval_to_transcript("txA", 18, 32)
    expected = ref.TXA_G_INTERVAL_18_32
    assert out.status is Status.PARTIAL
    assert _frag_dicts(out) == expected["fragments"]
    assert _gap_dicts(out) == expected["gaps"]
    assert out.mapped_length == expected["mapped_length"]


def test_plus_strand_transcript_interval_splits_across_exons(mapper):
    out = mapper.transcript_interval_to_genomic("txA", 8, 12)
    expected = ref.TXA_T_INTERVAL_8_12
    assert out.status is Status.OK
    assert _frag_dicts(out) == expected["fragments"]
    assert out.gaps == ()
    # Length conservation: every transcript base is exonic, so the mapped
    # length must equal the input interval length.
    assert out.mapped_length == 12 - 8


def test_minus_strand_transcript_interval_fragment_order(mapper):
    out = mapper.transcript_interval_to_genomic("txB", 18, 22)
    expected = ref.TXB_T_INTERVAL_18_22
    assert out.status is Status.OK
    assert _frag_dicts(out) == expected["fragments"]
    assert out.mapped_length == 22 - 18


def test_minus_strand_genomic_interval_fragments_follow_transcript_order(mapper):
    """Minus strand: fragments are ordered 5'->3' on the transcript, which is
    genomically DESCENDING. This is the order-preservation invariant."""
    out = mapper.genomic_interval_to_transcript("txB", 48, 82)
    expected = ref.TXB_G_INTERVAL_48_82
    assert out.status is Status.PARTIAL
    assert _frag_dicts(out) == expected["fragments"]
    assert _gap_dicts(out) == expected["gaps"]
    # First fragment is genomically downstream of the second: transcript order.
    assert out.fragments[0].g_start > out.fragments[1].g_start


def test_partially_overlapping_interval_reports_out_of_transcript_gap(mapper):
    out = mapper.genomic_interval_to_transcript("txA", 5, 12)
    expected = ref.TXA_G_INTERVAL_5_12
    assert out.status is Status.PARTIAL
    assert _frag_dicts(out) == expected["fragments"]
    assert _gap_dicts(out) == expected["gaps"]


def test_fully_intronic_interval_rejected(mapper):
    out = mapper.genomic_interval_to_transcript("txA", 20, 30)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INTRONIC
    assert out.fragments == ()
    assert len(out.gaps) == 1
    assert out.gaps[0].reason is Reason.INTRONIC


def test_interval_past_last_exon_rejected(mapper):
    out = mapper.genomic_interval_to_transcript("txA", 70, 75)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.OUT_OF_TRANSCRIPT


def test_full_length_minus_transcript_interval(mapper):
    out = mapper.transcript_interval_to_genomic("txB", 0, 40)
    expected = ref.TXB_T_INTERVAL_FULL
    assert out.status is Status.OK
    assert _frag_dicts(out) == expected["fragments"]
    assert out.mapped_length == 40


def test_invalid_intervals_rejected(mapper):
    out = mapper.genomic_interval_to_transcript("txA", 30, 20)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INVALID_INTERVAL
    out = mapper.genomic_interval_to_transcript("txA", 10, 10)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INVALID_INTERVAL
    out = mapper.genomic_interval_to_transcript("txA", -5, 10)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.INVALID_POSITION
    out = mapper.genomic_interval_to_transcript("txA", 0, 121)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.OUT_OF_CONTIG
    out = mapper.transcript_interval_to_genomic("txA", 0, 36)
    assert out.status is Status.REJECTED
    assert out.reason is Reason.OUT_OF_TRANSCRIPT


def test_length_conservation_law(mapper):
    """fragments + gaps always partition the requested genomic interval."""
    for tx_id, start, end in [("txA", 0, 120), ("txB", 10, 110), ("txC", 12, 75)]:
        out = mapper.genomic_interval_to_transcript(tx_id, start, end)
        covered = sum(f.length for f in out.fragments) + sum(
            g.g_end - g.g_start for g in out.gaps
        )
        assert covered == end - start
