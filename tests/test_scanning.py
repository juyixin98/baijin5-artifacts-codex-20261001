"""Scanner behaviour: strand coordinate mapping, window boundaries,
unknown-base policies, overlapping-hit identity."""
import pytest

from app.errors import UnknownBasePolicyError
from app.scanning import reverse_complement, scan_sequence
from tests.reference_data import (
    MARG_AN,
    MARG_CN,
    MARG_NG,
    MARG_NT,
    SCAN_CAGT_EXPECTED,
    SCORE_AG,
)


def _index(results):
    return {(w.start, w.strand): w for w in results}


def test_reverse_complement():
    assert reverse_complement("ACGT") == "ACGT"
    assert reverse_complement("CT") == "AG"
    assert reverse_complement("AAGN") == "NCTT"


def test_scan_scores_match_hand_computed(m2_pwm):
    results = scan_sequence("s1", "CAGT", m2_pwm)
    assert len(results) == 6  # 3 windows x 2 strands
    by_pos = _index(results)
    for key, expected in SCAN_CAGT_EXPECTED.items():
        w = by_pos[key]
        assert w.status == "scored"
        assert w.score == pytest.approx(expected), key


def test_minus_strand_coordinates_map_to_plus_strand(m2_pwm):
    # "CT" on the plus strand is "AG" on the minus strand: the hit must be
    # reported at plus-strand coordinates [0, 2) with strand '-'.
    results = scan_sequence("s1", "CT", m2_pwm)
    by_pos = _index(results)
    minus = by_pos[(0, "-")]
    assert minus.score == pytest.approx(SCORE_AG)
    assert (minus.start, minus.end, minus.strand) == (0, 2, "-")
    assert minus.matched_sequence == "AG"
    plus = by_pos[(0, "+")]
    assert plus.score == pytest.approx(-2.0)
    assert plus.matched_sequence == "CT"


def test_window_boundary_exact_length(m2_pwm):
    # sequence length == motif length -> exactly one window per strand
    results = scan_sequence("s1", "AG", m2_pwm)
    assert len(results) == 2
    assert {w.strand for w in results} == {"+", "-"}


def test_window_boundary_shorter_than_motif(m2_pwm):
    assert scan_sequence("s1", "A", m2_pwm) == []


def test_overlapping_hits_keep_distinct_identity(m2_pwm):
    # "AGAG": plus-strand windows at 0 and 2 both score SCORE_AG and must
    # both survive as separate results.
    results = scan_sequence("s1", "AGAG", m2_pwm)
    top = [w for w in results if w.score == pytest.approx(SCORE_AG)]
    assert len(top) == 2
    assert {(w.seq_id, w.start, w.strand) for w in top} == {
        ("s1", 0, "+"),
        ("s1", 2, "+"),
    }


def test_unknown_base_skip_policy(m2_pwm):
    results = scan_sequence("s1", "ANG", m2_pwm, unknown_policy="skip")
    assert len(results) == 4  # 2 windows x 2 strands
    assert all(w.status == "skipped" for w in results)
    assert all(w.reason == "unknown_base" for w in results)
    assert all(w.score is None for w in results)


def test_unknown_base_marginalize_policy(m2_pwm):
    results = scan_sequence("s1", "ANG", m2_pwm, unknown_policy="marginalize")
    by_pos = _index(results)
    assert by_pos[(0, "+")].score == pytest.approx(MARG_AN)  # AN
    assert by_pos[(1, "+")].score == pytest.approx(MARG_NG)  # NG
    assert by_pos[(0, "-")].score == pytest.approx(MARG_NT)  # RC(AN)=NT
    assert by_pos[(1, "-")].score == pytest.approx(MARG_CN)  # RC(NG)=CN
    assert all(w.status == "scored" for w in results)


def test_invalid_policy_rejected(m2_pwm):
    with pytest.raises(UnknownBasePolicyError) as excinfo:
        scan_sequence("s1", "ACGT", m2_pwm, unknown_policy="guess")
    assert excinfo.value.category == "unknown_base_policy"
