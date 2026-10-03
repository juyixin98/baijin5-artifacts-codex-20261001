"""Strand-aware scanning: coordinates, boundaries, unknown bases, corrections."""

import pytest

from app.errors import DomainError, ErrorCategory
from app.pwm import build_pwm
from app.scan import reverse_complement, scan_records
from app.sequence import SequenceRecord
from app.significance import enumerate_score_distribution
from tests.reference_motif import (
    CONSENSUS_LOGODDS,
    OFF_LOGODDS,
    P_BEST,
    REF_BACKGROUND,
    REF_COUNTS,
    REF_PSEUDOCOUNT,
    SCORE_BEST,
    naive_reverse_complement,
)


def make_scanner(counts=REF_COUNTS, background=None, pseudocount=REF_PSEUDOCOUNT):
    pwm = build_pwm(counts, background or dict(REF_BACKGROUND), pseudocount, max_motif_length=10)
    dist = enumerate_score_distribution(pwm)
    return pwm, dist


def scan(seq, threshold=SCORE_BEST, policy="skip", **kw):
    pwm, dist = make_scanner(**kw)
    return scan_records(pwm, dist, [SequenceRecord("s1", seq)], threshold, policy)


def test_forward_hit_coordinates_and_score():
    outcome = scan("GTACC")
    fwd = [h for h in outcome.hits if h.strand == "+"]
    assert len(fwd) == 1
    hit = fwd[0]
    assert (hit.start, hit.end, hit.matched) == (2, 4, "AC")
    assert hit.score == pytest.approx(SCORE_BEST)
    assert hit.pvalue == pytest.approx(P_BEST)


def test_reverse_hit_reported_on_forward_coordinates():
    outcome = scan("GTACC")
    rev = [h for h in outcome.hits if h.strand == "-"]
    assert len(rev) == 1
    hit = rev[0]
    # revcomp("GT") == "AC": the reverse-strand hit keeps forward coordinates
    # and the forward-strand window sequence as its identity.
    assert (hit.start, hit.end, hit.matched) == (0, 2, "GT")
    assert hit.score == pytest.approx(SCORE_BEST)


def test_reverse_complement_coordinate_mapping():
    # Property: a '-' hit at [s, e) in seq must appear as a '+' hit at
    # [L-e, L-s) in reverse_complement(seq), and vice versa.
    seq = "GTACC"
    length = len(seq)
    rc = naive_reverse_complement(seq)
    assert reverse_complement(seq) == rc  # package helper matches reference

    original = scan(seq)
    flipped = scan(rc)
    orig_minus = {(h.start, h.end) for h in original.hits if h.strand == "-"}
    orig_plus = {(h.start, h.end) for h in original.hits if h.strand == "+"}
    flip_plus = {(h.start, h.end) for h in flipped.hits if h.strand == "+"}
    flip_minus = {(h.start, h.end) for h in flipped.hits if h.strand == "-"}
    assert {(length - e, length - s) for s, e in orig_minus} == flip_plus
    assert {(length - e, length - s) for s, e in orig_plus} == flip_minus


def test_overlapping_hits_keep_individual_identity():
    counts = [[4.0, 0.0, 0.0, 0.0], [4.0, 0.0, 0.0, 0.0]]  # "AA" detector
    outcome = scan("AAAA", counts=counts)
    fwd = sorted((h.start, h.end) for h in outcome.hits if h.strand == "+")
    # Three overlapping windows, three distinct hits — none collapsed.
    assert fwd == [(0, 2), (1, 3), (2, 4)]


def test_hit_at_last_window_boundary():
    outcome = scan("GTAC")  # L == 4, motif k == 2
    fwd = [h for h in outcome.hits if h.strand == "+"]
    assert len(fwd) == 1
    assert (fwd[0].start, fwd[0].end) == (2, 4)  # window ends exactly at L


def test_sequence_length_equal_to_motif_gives_one_window_per_strand():
    outcome = scan("AC")
    assert outcome.evaluated_windows == 2
    starts = {(h.strand, h.start) for h in outcome.hits}
    assert ("+", 0) in starts  # AC itself scores the maximum


def test_sequence_shorter_than_motif_warns_and_evaluates_nothing():
    outcome = scan("A")
    assert outcome.evaluated_windows == 0
    assert outcome.hits == []
    assert any(w["category"] == "SEQUENCE_SHORTER_THAN_MOTIF" for w in outcome.warnings)


def test_unknown_bases_skip_policy_counts_skipped_windows():
    outcome = scan("ANC", policy="skip")
    # 2 forward + 2 reverse windows, every one contains an unknown base.
    assert outcome.skipped_windows == 4
    assert outcome.evaluated_windows == 0
    assert outcome.hits == []


def test_unknown_bases_marginalize_policy_scores_window():
    outcome = scan("ANC", threshold=-1e9, policy="marginalize")
    assert outcome.skipped_windows == 0
    assert outcome.evaluated_windows == 4
    marg = 0.25 * (CONSENSUS_LOGODDS + 3 * OFF_LOGODDS)
    by_window = {(h.strand, h.start): h for h in outcome.hits}
    # "AN": known A at position 0, marginalized position 1.
    assert by_window[("+", 0)].score == pytest.approx(CONSENSUS_LOGODDS + marg)
    # "NC": marginalized position 0, known C at position 1.
    assert by_window[("+", 1)].score == pytest.approx(marg + CONSENSUS_LOGODDS)


def test_invalid_unknown_policy_is_rejected():
    pwm, dist = make_scanner()
    with pytest.raises(DomainError) as excinfo:
        scan_records(pwm, dist, [SequenceRecord("s1", "ACGT")], 0.0, "interpolate")
    assert excinfo.value.category is ErrorCategory.UNKNOWN_POLICY_INVALID


def test_multiple_testing_correction_over_all_evaluated_windows():
    # "ACAC": 3 windows x 2 strands = 6 evaluated windows.
    # Hand-computed: two hits at p = 1/16 -> Bonferroni 6/16 = 0.375,
    # Benjamini-Hochberg (1/16)*6/2 = 0.1875.
    outcome = scan("ACAC")
    assert outcome.evaluated_windows == 6
    hits = [h for h in outcome.hits if h.strand == "+"]
    assert [h.start for h in hits] == [0, 2]
    for hit in hits:
        assert hit.pvalue == pytest.approx(P_BEST)
        assert hit.bonferroni == pytest.approx(0.375)
        assert hit.benjamini_hochberg == pytest.approx(0.1875)
