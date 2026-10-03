"""PWM construction and scoring against hand-computed reference values."""
import math

import pytest

from app.config import BackgroundModel
from app.errors import (
    BackgroundNotNormalizedError,
    InvalidMotifMatrixError,
    InvalidPseudocountError,
    UnknownBasePolicyError,
    ZeroBackgroundProbabilityError,
)
from app.pwm import PWM
from tests.reference_data import (
    LOG2_2_5,
    M2_MATRIX,
    M2_PSEUDOCOUNT,
    SCORE_AG,
    SCORE_LOW,
    SCORE_MID,
)


def test_probabilities_are_pseudocount_smoothed(m2_pwm):
    # (4+1)/8 and (0+1)/8, derived by hand in reference_data.py
    assert m2_pwm.probs[0, 0] == pytest.approx(5 / 8)
    assert m2_pwm.probs[0, 1] == pytest.approx(1 / 8)
    assert m2_pwm.probs[1, 2] == pytest.approx(5 / 8)
    for row in m2_pwm.probs:
        assert sum(row) == pytest.approx(1.0)


def test_log_odds_values(m2_pwm):
    assert m2_pwm.log_odds[0, 0] == pytest.approx(LOG2_2_5)  # A at pos1
    assert m2_pwm.log_odds[0, 1] == pytest.approx(-1.0)      # C at pos1
    assert m2_pwm.log_odds[1, 2] == pytest.approx(LOG2_2_5)  # G at pos2


def test_score_matches_hand_computed_values(m2_pwm):
    assert m2_pwm.score("AG") == pytest.approx(SCORE_AG)
    assert m2_pwm.score("AA") == pytest.approx(SCORE_MID)
    assert m2_pwm.score("CG") == pytest.approx(SCORE_MID)
    assert m2_pwm.score("TT") == pytest.approx(SCORE_LOW)


def test_score_with_nonuniform_background():
    # Hand check: pos1 A prob 5/8 vs background 0.4 ->
    # log2((5/8)/0.4) = log2(1.5625) = 0.6438561897747247
    bg = BackgroundModel({"A": 0.4, "C": 0.1, "G": 0.1, "T": 0.4})
    pwm = PWM(matrix=[row[:] for row in M2_MATRIX], background=bg, pseudocount=1.0)
    assert pwm.log_odds[0, 0] == pytest.approx(0.6438561897747247)


def test_unknown_base_without_policy_is_a_declared_error(m2_pwm):
    with pytest.raises(UnknownBasePolicyError) as excinfo:
        m2_pwm.score("AN")
    assert excinfo.value.category == "unknown_base_policy"


def test_zero_background_probability_rejected():
    with pytest.raises(ZeroBackgroundProbabilityError) as excinfo:
        BackgroundModel({"A": 0.5, "C": 0.5, "G": 0.0, "T": 0.0})
    assert excinfo.value.category == "zero_background_probability"
    assert excinfo.value.details["base"] == "G"


def test_unnormalized_background_rejected():
    with pytest.raises(BackgroundNotNormalizedError) as excinfo:
        BackgroundModel({"A": 0.5, "C": 0.2, "G": 0.2, "T": 0.2})
    assert excinfo.value.category == "background_not_normalized"


def test_background_missing_base_rejected():
    with pytest.raises(BackgroundNotNormalizedError):
        BackgroundModel({"A": 0.5, "C": 0.5})


def test_invalid_matrix_shape_rejected(uniform_background):
    with pytest.raises(InvalidMotifMatrixError) as excinfo:
        PWM(matrix=[[1.0, 2.0, 3.0]], background=uniform_background, pseudocount=1.0)
    assert excinfo.value.category == "invalid_motif_matrix"


def test_negative_count_rejected(uniform_background):
    with pytest.raises(InvalidMotifMatrixError):
        PWM(matrix=[[-1.0, 0.0, 0.0, 0.0]], background=uniform_background, pseudocount=1.0)


def test_nonpositive_pseudocount_rejected(uniform_background):
    for bad in (0.0, -0.5, math.inf):
        with pytest.raises(InvalidPseudocountError) as excinfo:
            PWM(matrix=[row[:] for row in M2_MATRIX], background=uniform_background, pseudocount=bad)
        assert excinfo.value.category == "invalid_pseudocount"
