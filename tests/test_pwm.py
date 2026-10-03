"""PWM construction: hand-computed values and every declared failure category."""

import math

import pytest

from app.errors import DomainError, ErrorCategory
from app.pwm import build_pwm
from tests.reference_motif import (
    CONSENSUS_LOGODDS,
    OFF_LOGODDS,
    REF_BACKGROUND,
    REF_COUNTS,
    REF_PSEUDOCOUNT,
)

MAX_K = 10


def build(counts=REF_COUNTS, background=None, pseudocount=REF_PSEUDOCOUNT):
    return build_pwm(counts, background or dict(REF_BACKGROUND), pseudocount, MAX_K)


def test_log_odds_match_hand_computed_values():
    pwm = build()
    assert pwm.length == 2
    # Position 0: A (column 0) is consensus; position 1: C (column 1).
    assert pwm.log_odds[0, 0] == pytest.approx(CONSENSUS_LOGODDS)
    assert pwm.log_odds[1, 1] == pytest.approx(CONSENSUS_LOGODDS)
    for b in (1, 2, 3):
        assert pwm.log_odds[0, b] == pytest.approx(OFF_LOGODDS)
    for b in (0, 2, 3):
        assert pwm.log_odds[1, b] == pytest.approx(OFF_LOGODDS)


def test_zero_background_probability_is_rejected_with_category():
    bg = {"A": 0.0, "C": 1.0 / 3, "G": 1.0 / 3, "T": 1.0 / 3}
    with pytest.raises(DomainError) as excinfo:
        build(background=bg)
    assert excinfo.value.category is ErrorCategory.ZERO_BACKGROUND_PROBABILITY
    assert excinfo.value.detail["base"] == "A"


def test_negative_background_probability_is_also_zero_category():
    bg = {"A": -0.1, "C": 0.4, "G": 0.4, "T": 0.3}
    with pytest.raises(DomainError) as excinfo:
        build(background=bg)
    assert excinfo.value.category is ErrorCategory.ZERO_BACKGROUND_PROBABILITY


def test_unnormalized_background_is_rejected():
    bg = {"A": 0.7, "C": 0.2, "G": 0.2, "T": 0.2}
    with pytest.raises(DomainError) as excinfo:
        build(background=bg)
    assert excinfo.value.category is ErrorCategory.BACKGROUND_NOT_NORMALIZED


def test_missing_background_base_is_rejected():
    with pytest.raises(DomainError) as excinfo:
        build(background={"A": 0.25, "C": 0.25, "G": 0.5})
    assert excinfo.value.category is ErrorCategory.INVALID_BACKGROUND


def test_nonpositive_pseudocount_is_rejected():
    with pytest.raises(DomainError) as excinfo:
        build(pseudocount=0.0)
    assert excinfo.value.category is ErrorCategory.INVALID_PSEUDOCOUNT


def test_ragged_motif_is_rejected():
    with pytest.raises(DomainError) as excinfo:
        build(counts=[[1, 1, 1, 1], [1, 1]])
    assert excinfo.value.category is ErrorCategory.INVALID_MOTIF


def test_wrong_column_count_is_rejected():
    with pytest.raises(DomainError) as excinfo:
        build(counts=[[1, 1, 1]])
    assert excinfo.value.category is ErrorCategory.INVALID_MOTIF


def test_motif_longer_than_enumeration_limit_is_rejected():
    counts = [[1.0, 1.0, 1.0, 1.0]] * (MAX_K + 1)
    with pytest.raises(DomainError) as excinfo:
        build(counts=counts)
    assert excinfo.value.category is ErrorCategory.MOTIF_TOO_LONG


def test_zero_count_position_is_rejected():
    with pytest.raises(DomainError) as excinfo:
        build(counts=[[4, 0, 0, 0], [0, 0, 0, 0]])
    assert excinfo.value.category is ErrorCategory.INVALID_MOTIF
    assert excinfo.value.detail["zero_count_positions"] == [1]


def test_nonuniform_background_changes_log_odds():
    bg = {"A": 0.4, "C": 0.1, "G": 0.1, "T": 0.4}
    pwm = build(background=bg)
    # p'_{0,A} = (4 + 0.4) / 5 = 0.88 -> log2(0.88/0.4) = log2(2.2)
    assert pwm.log_odds[0, 0] == pytest.approx(math.log2(2.2))
