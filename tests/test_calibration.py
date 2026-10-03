"""Exact score-distribution enumeration: p-values and thresholds checked
against hand-computed reference values."""
import pytest

from app.calibration import enumerate_score_distribution
from app.config import BackgroundModel
from app.errors import InvalidAlphaError, MotifTooLongError
from app.pwm import PWM
from tests.reference_data import P_AG, P_LOW, P_MID, SCORE_AG, SCORE_LOW, SCORE_MID


def test_distribution_has_three_score_classes(m2_pwm, uniform_background):
    dist = enumerate_score_distribution(m2_pwm, uniform_background)
    assert dist.n_distinct == 3
    assert dist.scores == pytest.approx([SCORE_LOW, SCORE_MID, SCORE_AG])
    assert dist.tail_probs == pytest.approx([P_LOW, P_MID, P_AG])


def test_pvalues_match_hand_computed_tails(m2_pwm, uniform_background):
    dist = enumerate_score_distribution(m2_pwm, uniform_background)
    assert dist.pvalue(SCORE_AG) == pytest.approx(P_AG)
    assert dist.pvalue(SCORE_MID) == pytest.approx(P_MID)
    assert dist.pvalue(SCORE_LOW) == pytest.approx(P_LOW)


def test_pvalue_above_max_score_is_zero(m2_pwm, uniform_background):
    dist = enumerate_score_distribution(m2_pwm, uniform_background)
    assert dist.pvalue(SCORE_AG + 1.0) == 0.0


def test_threshold_for_alpha(m2_pwm, uniform_background):
    dist = enumerate_score_distribution(m2_pwm, uniform_background)

    t = dist.threshold_for_alpha(0.1)
    assert t.achievable and t.score == pytest.approx(SCORE_AG)
    assert t.achieved_alpha == pytest.approx(P_AG)

    t = dist.threshold_for_alpha(0.5)
    assert t.achievable and t.score == pytest.approx(SCORE_MID)
    assert t.achieved_alpha == pytest.approx(P_MID)

    t = dist.threshold_for_alpha(1.0)
    assert t.achievable and t.score == pytest.approx(SCORE_LOW)
    assert t.achieved_alpha == pytest.approx(1.0)


def test_threshold_unachievable_for_strict_alpha(m2_pwm, uniform_background):
    # smallest attainable tail is 1/16 = 0.0625 > 0.05
    dist = enumerate_score_distribution(m2_pwm, uniform_background)
    t = dist.threshold_for_alpha(0.05)
    assert not t.achievable
    assert t.score is None
    assert t.achieved_alpha == 0.0


def test_threshold_corresponds_to_declared_background(m2_pwm):
    # Same motif, different declared background -> different null tail.
    # Hand check (corrected after an initial mis-derivation, see README):
    # under bg A=T=0.4, C=G=0.1 the log-odds are
    #   pos1: A = log2(0.625/0.4) = log2(1.5625) = 0.643856...
    #   pos2: G = log2(0.625/0.1) = log2(6.25)   = 2.643856...
    # so AG alone attains the maximal score 3.287712... (next best is
    # {C,G}G at 0.321928... + 2.643856... = 2.965784...), and
    # P(S >= max) = P(AG) = 0.4 * 0.1 = 0.04 exactly.
    bg = BackgroundModel({"A": 0.4, "C": 0.1, "G": 0.1, "T": 0.4})
    pwm = PWM(matrix=[[4.0, 0, 0, 0], [0, 0, 4.0, 0]], background=bg, pseudocount=1.0)
    dist = enumerate_score_distribution(pwm, bg)
    assert dist.max_score == pytest.approx(3.287712119549449)
    assert dist.pvalue(dist.max_score) == pytest.approx(0.04)


def test_invalid_alpha_rejected(m2_pwm, uniform_background):
    dist = enumerate_score_distribution(m2_pwm, uniform_background)
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(InvalidAlphaError) as excinfo:
            dist.threshold_for_alpha(bad)
        assert excinfo.value.category == "invalid_alpha"


def test_motif_too_long_for_exact_calibration(uniform_background):
    long_matrix = [[1.0, 1.0, 1.0, 1.0]] * 11  # 4^11 k-mers > declared limit
    pwm = PWM(matrix=long_matrix, background=uniform_background, pseudocount=1.0)
    with pytest.raises(MotifTooLongError) as excinfo:
        enumerate_score_distribution(pwm, uniform_background)
    assert excinfo.value.category == "motif_too_long_for_exact_calibration"
