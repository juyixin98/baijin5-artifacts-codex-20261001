"""Exact score distribution, p-values and multiple-testing correction."""

import pytest

from app.pwm import build_pwm
from app.significance import (
    benjamini_hochberg,
    bonferroni,
    enumerate_score_distribution,
    pvalue_at_least,
    threshold_for_pvalue,
)
from tests.reference_motif import (
    P_BEST,
    P_LOW,
    P_MID,
    REF_BACKGROUND,
    REF_COUNTS,
    REF_PSEUDOCOUNT,
    SCORE_BEST,
    SCORE_LOW,
    SCORE_MID,
    naive_distribution,
    naive_pvalue,
)


@pytest.fixture()
def ref_dist():
    pwm = build_pwm(REF_COUNTS, dict(REF_BACKGROUND), REF_PSEUDOCOUNT, max_motif_length=10)
    return enumerate_score_distribution(pwm)


def test_enumeration_covers_all_words(ref_dist):
    assert ref_dist.n_words == 16
    # Hand-computed: exactly three distinct scores.
    assert ref_dist.scores.tolist() == pytest.approx([SCORE_LOW, SCORE_MID, SCORE_BEST])
    # Hand-computed tail probabilities P(S >= s).
    assert ref_dist.tail_probs.tolist() == pytest.approx([P_LOW, P_MID, P_BEST])


def test_pvalues_match_hand_computed_values(ref_dist):
    assert pvalue_at_least(ref_dist, SCORE_BEST) == pytest.approx(P_BEST)
    assert pvalue_at_least(ref_dist, SCORE_MID) == pytest.approx(P_MID)
    assert pvalue_at_least(ref_dist, SCORE_LOW) == pytest.approx(P_LOW)


def test_pvalue_outside_score_range(ref_dist):
    assert pvalue_at_least(ref_dist, SCORE_BEST + 1.0) == 0.0
    assert pvalue_at_least(ref_dist, SCORE_LOW - 1.0) == 1.0


def test_threshold_for_pvalue(ref_dist):
    # 0.05 is stricter than the best achievable p (0.0625) -> unreachable.
    assert threshold_for_pvalue(ref_dist, 0.05) is None
    assert threshold_for_pvalue(ref_dist, P_BEST) == pytest.approx(SCORE_BEST)
    assert threshold_for_pvalue(ref_dist, 0.1) == pytest.approx(SCORE_BEST)
    assert threshold_for_pvalue(ref_dist, 0.5) == pytest.approx(SCORE_MID)
    assert threshold_for_pvalue(ref_dist, 1.0) == pytest.approx(SCORE_LOW)


def test_distribution_matches_independent_naive_enumeration():
    # A different motif (k=3) with a non-uniform background, cross-checked
    # against the naive pure-Python reference implementation.
    counts = [[3.0, 1.0, 0.0, 0.0], [0.0, 0.0, 4.0, 0.0], [1.0, 0.0, 0.0, 3.0]]
    background = {"A": 0.4, "C": 0.1, "G": 0.1, "T": 0.4}
    pseudocount = 0.8
    pwm = build_pwm(counts, background, pseudocount, max_motif_length=10)
    dist = enumerate_score_distribution(pwm)
    assert dist.n_words == 64

    naive = naive_distribution(counts, background, pseudocount)
    assert dist.scores.tolist() == pytest.approx(sorted(naive), abs=1e-9)
    for score, prob in zip(dist.scores, dist.tail_probs):
        # tail prob from the package vs. independent tail computation
        naive_tail = sum(p for s, p in naive.items() if s >= score - 1e-9)
        assert prob == pytest.approx(naive_tail, abs=1e-12)

    # And a couple of spot p-values against the naive implementation.
    for word_score in sorted(naive)[::7]:
        assert pvalue_at_least(dist, word_score) == pytest.approx(
            naive_pvalue(counts, background, pseudocount, word_score), abs=1e-12
        )


def test_bonferroni_hand_computed():
    # m = 6 evaluated windows; 7/16 * 6 = 2.625 is clipped at 1.0.
    assert bonferroni([P_BEST, P_MID], m=6) == pytest.approx([0.375, 1.0])
    assert bonferroni([0.9], m=6) == pytest.approx([1.0])  # clipped at 1


def test_benjamini_hochberg_hand_computed():
    # p-values from scanning "ACAC" with the reference motif (6 windows):
    # two windows at 1/16, one at 7/16, three at 1.0.
    pvals = [P_BEST, P_MID, P_BEST, 1.0, 1.0, 1.0]
    adjusted = benjamini_hochberg(pvals, m=6)
    # rank1: (1/16)*6/1 = 0.375, rank2: (1/16)*6/2 = 0.1875, rank3: (7/16)*6/3 = 0.875
    # cumulative min from the top -> both 1/16 hits get 0.1875, the 7/16 window 0.875.
    assert adjusted[0] == pytest.approx(0.1875)
    assert adjusted[2] == pytest.approx(0.1875)
    assert adjusted[1] == pytest.approx(0.875)
    assert adjusted[3:] == pytest.approx([1.0, 1.0, 1.0])


def test_corrections_with_no_tests_are_safe():
    assert bonferroni([], m=0) == []
    assert benjamini_hochberg([], m=0) == []
