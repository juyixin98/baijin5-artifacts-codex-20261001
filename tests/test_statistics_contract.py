"""Tests for the LORD++ statistical kernel and its frozen contract.

Reference numbers below were produced by an INDEPENDENT stdlib-only oracle
(see scripts/hand_oracle_check / tests/test_reference_oracle.py) and pasted in
as literals; they are not computed from the kernel under test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.errors import (
    ComputationError,
    InputValidationError,
    ResourceExhaustedError,
)
from app.statistics import (
    CONTRACT_VERSION,
    DEFAULT_ALPHA,
    DEFAULT_W0_FRACTION,
    SCHEDULE_C,
    SCHEDULE_HORIZON,
    LordConfig,
    LordState,
    normalized_schedule,
    validate_pvalue,
)

# Frozen five-step oracle trajectory (alpha=0.05, w0=0.045, H=1000).
# Produced with a separate math.fsum script that never imported app.statistics.
HAND_PS = [0.0005, 0.5, 0.0005, 0.9, 0.0004]
HAND_S_H = 1.742601339689304
HAND = [
    # (t, gamma_t, wealth W_t, alpha_t, rejected) — canonical LORD++ recursion
    # net reward per rejection k is (b - alpha_{tau_k}), not (b - p_{tau_k}).
    (1, 0.01435934464659336, 0.045, 0.0006461705090967012, True),
    (2, 0.01435934464659336, 0.045062518138192384, 0.000647068228589669, False),
    (3, 0.015172681866521041, 0.045062518138192384, 0.0006837192518151271, True),
    (4, 0.01435934464659336, 0.045128038232621195, 0.0006480090542068497, False),
    (5, 0.013336546303638225, 0.045124497101047024, 0.0006018049450165025, True),
]


def _state() -> LordState:
    return LordState.initialize(LordConfig.create(alpha=0.05, w0=0.045, horizon=1000))


def test_hand_trajectory_matches_independent_oracle_literals():
    st = _state()
    for (t, g, w_before, alpha_t, rejected), p in zip(HAND, HAND_PS):
        preview = st.preview_threshold()
        step = st.step(p)
        assert step.index == t
        assert math.isclose(step.gamma_t, g, rel_tol=1e-12, abs_tol=1e-15)
        assert math.isclose(step.wealth_before, w_before, rel_tol=1e-12, abs_tol=1e-14)
        assert math.isclose(step.threshold, alpha_t, rel_tol=1e-12, abs_tol=1e-15)
        # The pre-committed preview and the actually applied threshold are the
        # same number: p_t cannot influence alpha_t.
        assert preview == step.threshold
        assert step.rejected is rejected


def test_rejection_times_are_exactly_one_three_five():
    st = _state()
    times = []
    for p in HAND_PS:
        s = st.step(p)
        if s.rejected:
            times.append(s.index)
    assert times == [1, 3, 5]


def test_schedule_normalization_constant_and_mass():
    sched = normalized_schedule(1000)
    # Independent accumulation of the unnormalized series:
    raw_total = math.fsum(
        SCHEDULE_C * math.log(max(j, 2)) / max(j, 2) for j in range(1, 1001)
    )
    assert raw_total == pytest.approx(HAND_S_H, rel=1e-13)
    assert math.fsum(sched.tolist()) == pytest.approx(1.0, abs=1e-12)
    assert sched[0] == pytest.approx(
        (SCHEDULE_C * math.log(2) / 2) / HAND_S_H, rel=1e-13
    )
    assert np.all(sched > 0.0)


def test_threshold_depends_only_on_past_results():
    """Same history, different p_t at the next slot => identical alpha_t.

    And after identical decisions the futures coincide; futures diverge only
    when the present decision differs.
    """
    p_history = [0.5, 0.0004, 0.7]
    s_a = _state()
    s_b = _state()
    for p in p_history:
        s_a.step(p)
        s_b.step(p)
    a_threshold = s_a.preview_threshold()
    # Observer B is free to claim any p-value next; its pre-observation
    # threshold is fixed before p is known.
    assert s_b.preview_threshold() == a_threshold
    s_a.step(0.9)     # not rejected
    s_b.step(0.2)     # not rejected (threshold ~ 6e-4, both keep)
    assert s_a.preview_threshold() == s_b.preview_threshold()


def test_threshold_tie_is_a_rejection():
    st = _state()
    alpha1 = st.preview_threshold()
    # p exactly equal to alpha_t rejects (<=)
    tie = st.step(alpha1)
    assert tie.rejected is True
    assert tie.p_value == tie.threshold


def test_wealth_reward_formula_frozen():
    """W_t = w0 + sum gamma(t-tau_k)(b - alpha_{tau_k}), checked at hand step 3.

    The level returned on rejection k is the threshold it SPENT (alpha_tau_k),
    which was frozen before p_{tau_k} was observed -- never the p-value.
    """
    st = _state()
    first = st.step(HAND_PS[0])     # reject at t=1; spends alpha_1
    st.step(HAND_PS[1])             # keep at t=2
    w3 = st.wealth                  # W_3
    b = 0.05 - 0.045
    sched = normalized_schedule(1000)
    expected = 0.045 + float(sched[3 - 1 - 1]) * (b - first.threshold)
    assert w3 == pytest.approx(expected, rel=1e-13)
    assert w3 == pytest.approx(HAND[2][2], rel=1e-12)
    # The spent level recorded is the frozen threshold, not the p-value.
    assert st.rejection_spent == [first.threshold]
    assert st.rejection_spent[0] != HAND_PS[0]


def test_invalid_p_values_are_rejected_not_repaired():
    st = _state()
    for bad in [0, -0.01, 1.0000001, float("nan"), float("inf"), "0.01", None, True]:
        with pytest.raises(InputValidationError) as ei:
            st.step(bad)
        assert ei.value.code == "INPUT_ERROR"
    # A rejected input must not advance state.
    assert st.steps_taken == 0
    assert st.rejection_times == []


def test_validate_pvalue_boundaries():
    assert validate_pvalue(1.0) == 1.0
    assert validate_pvalue(1e-300) == 1e-300
    with pytest.raises(InputValidationError):
        validate_pvalue(0.0)


def test_config_parameters_are_frozen_and_validated():
    cfg = LordConfig.create()
    assert cfg.contract_version == CONTRACT_VERSION
    assert cfg.alpha == DEFAULT_ALPHA
    assert cfg.w0 == pytest.approx(DEFAULT_ALPHA * DEFAULT_W0_FRACTION)
    assert cfg.payoff == pytest.approx(DEFAULT_ALPHA * (1 - DEFAULT_W0_FRACTION))
    assert cfg.horizon == SCHEDULE_HORIZON
    with pytest.raises(InputValidationError):
        LordConfig.create(alpha=0.0)
    with pytest.raises(InputValidationError):
        LordConfig.create(alpha=0.05, w0=0.06)   # w0 > alpha
    with pytest.raises(InputValidationError):
        LordConfig.create(alpha=0.05, w0=0.0)    # w0 must be > 0
    with pytest.raises(InputValidationError):
        LordConfig.create(horizon=0)
    with pytest.raises(InputValidationError):
        LordConfig.create(horizon="1000")        # type: ignore[arg-type]
    # Frozen dataclass: no post-creation mutation.
    with pytest.raises(Exception):
        cfg.alpha = 0.10  # type: ignore[misc]


def test_horizon_exhaustion_is_resource_exhausted():
    st = LordState.initialize(LordConfig.create(alpha=0.05, w0=0.045, horizon=3))
    st.step(0.5)
    st.step(0.5)
    st.step(0.5)
    with pytest.raises(ResourceExhaustedError) as ei:
        st.preview_threshold()
    assert ei.value.code == "RESOURCE_EXHAUSTED"
    with pytest.raises(ResourceExhaustedError):
        st.step(0.5)


def test_state_is_rebuilt_from_rejection_history_not_mutated_counter():
    st = _state()
    for p in HAND_PS:
        st.step(p)
    assert st.rejection_times == [1, 3, 5]
    # Spent levels are the pre-committed thresholds at those rejection times.
    assert st.rejection_spent == pytest.approx(
        [HAND[0][3], HAND[2][3], HAND[4][3]], rel=1e-12, abs=1e-15
    )
    assert st.steps_taken == 5
