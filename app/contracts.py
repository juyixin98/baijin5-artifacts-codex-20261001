"""Frozen statistical contract for LORD 3.

Source of truth
---------------
A. Javanmard and A. Montanari, "Online Rules for Control of False Discovery
Rate and False Discovery Proportion under Statistical Independence",
arXiv:1603.09000 (2018).

Frozen formulas (paper notation)
--------------------------------
* Threshold at time i:      alpha_i = gamma_{i - tau_i} * W(tau_i)   (Eq. 18)
* Wealth update at time t:  W(t) = W(t-1) - alpha_t
                                 + b0 * 1{p_t <= alpha_t}            (Eq. 9 + 15)
* Before the first discovery tau = 0 and W(0) = w0, so the very first
  threshold is gamma_1 * w0.
* gamma sequence (Eq. 31), with the m in {1, 2} log-guard used by the
  reference implementations:
      gamma_m = C * log(max(m, 2)) / (m * exp(sqrt(log(max(m, 2)))))
      C = 0.07720838
* Decision rule: reject H_i iff p_i <= alpha_i (closed interval).

Frozen parameter choice (the only parameter set the service serves)
-------------------------------------------------------------------
alpha = 0.05, w0 = 0.005, b0 = 0.045, satisfying
0 <= w0 <= alpha, b0 > 0, w0 + b0 <= alpha, and sum_m gamma_m <= 1
(empirical total ~= 0.935).

Reward timing
-------------
Paper-faithful timing is frozen here: the reward b0 enters the wealth on the
*same* step as the rejection, hence W(tau_i) already contains the reward
earned at tau_i.  Note: the R package onlineFDR's C++ `lord.cpp` (version=3)
uses a one-step-delayed reward R[i-1]*b0; this documented discrepancy changes
wealth/threshold numbers but not the rejection pattern on the package's own
teaching sample p = (1e-7, 0.1, 0.00025, 0.07), which is R = (1, 0, 1, 0)
under both conventions.

Applicability assumptions
-------------------------
* Hypotheses arrive in a fixed, pre-committed order; each is tested once.
* The p-value submitted at time i must be available (and the test that
  produced it chosen) before alpha_i is revealed.
* p-values are mutually independent; alternatively the null p-values are
  independent of the non-null ones.  LORD 3 does NOT rely on PRDS; the
  guarantee is FDR <= alpha (mFDR in the paper) under independence.
* p-values are valid: under the null P(p <= x) <= x for x in [0, 1], and the
  procedure is super-uniformity-based, not a re-analysis of a fixed batch.
* This is an online procedure; it must not be presented as, or replaced by,
  an offline Benjamini-Hochberg analysis.

Nothing in this module performs I/O.  The fingerprint hashes the frozen
constants so persisted evidence can prove which contract produced it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Final, Iterable

from .errors import (
    DuplicateHypothesisError,
    InvalidHypothesisIdError,
    InvalidPValueError,
    NonFiniteResultError,
    RunLimitReachedError,
)

RULE_NAME: Final = "LORD3"
RULE_VERSION: Final = "lord3-paper-v1"

# Frozen parameters (do not change; changing these invalidates evidence).
ALPHA: Final = 0.05
W0: Final = 0.005
B0: Final = 0.045
GAMMA_C: Final = 0.07720838

# Hard safety ceiling; runs may set a smaller max_decisions.
HARD_MAX_DECISIONS: Final = 1_000_000
DEFAULT_MAX_DECISIONS: Final = 100_000


def gamma_m(m: int) -> float:
    """gamma_m from paper Eq. 31.

    m is a positive 1-based index into the gamma sequence (i - tau_i).
    gamma_1 == gamma_2 because of the max(m, 2) guard on log(1) = 0.
    """
    if m <= 0:
        raise ValueError(f"gamma index must be positive, got {m}")
    x = max(m, 2)
    log_x = math.log(x)
    return GAMMA_C * log_x / (x * math.exp(math.sqrt(log_x)))


_FROZEN_CONSTANTS = {
    "rule": RULE_NAME,
    "rule_version": RULE_VERSION,
    "alpha": ALPHA,
    "w0": W0,
    "b0": B0,
    "gamma_C": GAMMA_C,
    "reward_timing": "same_step_paper_eq9_15",
    "reject_on": "p <= alpha (closed interval)",
    "gamma_formula": "C*log(max(m,2))/(m*exp(sqrt(log(max(m,2)))))",
}

CONTRACT_FINGERPRINT: Final = hashlib.sha256(
    json.dumps(_FROZEN_CONSTANTS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def contract_manifest() -> dict:
    """Serializable description of the frozen rule and its assumptions."""
    return {
        **_FROZEN_CONSTANTS,
        "fingerprint": CONTRACT_FINGERPRINT,
        "paper": "arXiv:1603.09000 (Javanmard & Montanari 2018)",
        "equations": {
            "threshold": "alpha_i = gamma_{i-tau_i} * W(tau_i)  [Eq. 18]",
            "wealth": "W(t)=W(t-1)-alpha_t+b0*1{p_t<=alpha_t}  [Eq. 9/15]",
            "gamma": "Eq. 31",
        },
        "constraints": {
            "w0_range": "0 <= w0 <= alpha",
            "b0": "b0 > 0",
            "w0_plus_b0": "w0 + b0 <= alpha",
            "gamma_sum": "<= 1 (numerically ~= 0.935 for C=0.07720838)",
        },
        "assumptions": [
            "fixed pre-committed hypothesis order, each hypothesis tested once",
            "p-value determined before the threshold is revealed",
            "mutually independent p-values, or null p-values independent "
            "of non-null",
            "valid (super-uniform under the null) p-values in [0, 1]",
            "online setting; offline BH is not a substitute",
        ],
        "onlinefdr_note": (
            "onlineFDR lord.cpp version=3 rewards one step late "
            "(R[i-1]*b0); this implementation freezes the paper's same-step "
            "timing. Teaching sample (1e-7, 0.1, 0.00025, 0.07) yields "
            "R=(1,0,1,0) under both."
        ),
    }


def _validate_hypothesis_id(hypothesis_id: object) -> str:
    if not isinstance(hypothesis_id, str):
        raise InvalidHypothesisIdError({"received_type": type(hypothesis_id).__name__})
    stripped = hypothesis_id.strip()
    if (
        len(hypothesis_id) == 0
        or len(hypothesis_id) > 256
        or stripped != hypothesis_id
        or any(ch.isspace() for ch in hypothesis_id)
    ):
        raise InvalidHypothesisIdError(
            {"length": len(hypothesis_id) if isinstance(hypothesis_id, str) else None}
        )
    return hypothesis_id


def _validate_p_value(p_value: object) -> float:
    if isinstance(p_value, bool) or not isinstance(p_value, (int, float)):
        raise InvalidPValueError({"received_type": type(p_value).__name__})
    p = float(p_value)
    if not math.isfinite(p) or p < 0.0 or p > 1.0:
        raise InvalidPValueError({"received": p_value})
    return p


@dataclass(frozen=True, slots=True)
class Decision:
    """One irrevocable online decision.  These rows are what gets persisted."""

    index: int
    hypothesis_id: str
    p_value: float
    threshold: float
    gamma_value: float
    rejected: bool
    wealth_before: float
    wealth_after: float
    tau: int            # most recent rejection time AFTER this step
    w_tau_used: float   # W(tau_i) used to build this step's threshold
    reason: str


@dataclass(frozen=True, slots=True)
class LORD3State:
    """Immutable kernel state.  ``step`` returns a new state, never mutates."""

    tau: int = 0
    w_tau: float = W0
    wealth: float = W0
    last_index: int = 0
    seen_ids: frozenset[str] = field(default_factory=frozenset)
    decisions: tuple[Decision, ...] = ()
    max_decisions: int = DEFAULT_MAX_DECISIONS

    @classmethod
    def initial(cls, max_decisions: int = DEFAULT_MAX_DECISIONS) -> "LORD3State":
        if not isinstance(max_decisions, int) or isinstance(max_decisions, bool):
            raise TypeError("max_decisions must be an int")
        if not (1 <= max_decisions <= HARD_MAX_DECISIONS):
            raise ValueError(
                f"max_decisions must be in [1, {HARD_MAX_DECISIONS}]"
            )
        return cls(max_decisions=max_decisions)


def step(state: LORD3State, hypothesis_id: str, p_value: object) -> tuple[
    LORD3State, Decision
]:
    """Test one hypothesis.

    Strictly online: the threshold is computed from past outcomes only
    (tau, W(tau)) and fixed before it is compared with the p-value.
    """
    hid = _validate_hypothesis_id(hypothesis_id)
    p = _validate_p_value(p_value)
    if hid in state.seen_ids:
        raise DuplicateHypothesisError(
            {"hypothesis_id": hid, "index": state.last_index + 1}
        )
    if state.last_index >= state.max_decisions:
        raise RunLimitReachedError({"max_decisions": state.max_decisions})

    index = state.last_index + 1
    gamma_index = index - state.tau
    g = gamma_m(gamma_index)
    threshold = g * state.w_tau
    if not math.isfinite(threshold):
        raise NonFiniteResultError({"at_index": index, "stage": "threshold"})

    rejected = p <= threshold  # closed interval, frozen decision rule
    wealth_before = state.wealth
    wealth_after = wealth_before - threshold + (B0 if rejected else 0.0)
    if not math.isfinite(wealth_after):
        raise NonFiniteResultError({"at_index": index, "stage": "wealth"})

    tau_after = index if rejected else state.tau
    w_tau_after = wealth_after if rejected else state.w_tau
    reason = (
        f"p={p:.6g} <= alpha_{index}={threshold:.6g} "
        f"(gamma_{gamma_index}={g:.9g} * W({state.tau})={state.w_tau:.9g})"
        if rejected
        else f"p={p:.6g} > alpha_{index}={threshold:.6g} "
        f"(gamma_{gamma_index}={g:.9g} * W({state.tau})={state.w_tau:.9g})"
    )
    decision = Decision(
        index=index,
        hypothesis_id=hid,
        p_value=p,
        threshold=threshold,
        gamma_value=g,
        rejected=rejected,
        wealth_before=wealth_before,
        wealth_after=wealth_after,
        tau=tau_after,
        w_tau_used=state.w_tau,
        reason=reason,
    )
    new_state = replace(
        state,
        tau=tau_after,
        w_tau=w_tau_after,
        wealth=wealth_after,
        last_index=index,
        seen_ids=state.seen_ids | {hid},
        decisions=state.decisions + (decision,),
    )
    return new_state, decision


def replay(pairs: Iterable[tuple[str, float]]) -> list[Decision]:
    """Replay an ordered sequence of (hypothesis_id, p_value) from scratch.

    Independent entry point used by the diagnostics layer to verify stored
    evidence: rebuilding the whole trajectory from the original p-values must
    reproduce every recorded threshold and decision.
    """
    state = LORD3State.initial(max_decisions=HARD_MAX_DECISIONS)
    out: list[Decision] = []
    for hid, p in pairs:
        state, decision = step(state, hid, p)
        out.append(decision)
    return out
