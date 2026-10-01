"""Statistical contract for online FDR control with the LORD++ rule.

The rule, its parameters, the initial wealth and the reward formula are all
FROZEN here. Nothing in this module reads mutable configuration; changing any
of these values changes the statistical guarantee and must be treated as a new
contract (hence the explicit ``CONTRACT_VERSION``).

Rule implemented (one rule only, as required):
    LORD++ of Ramdas, Zrnic & Wainwright (2018), "Sawr-LORD" / LORD++ form,
    asynchronous/online setting with independent or locally-dependent p-values.

Notation and update equations (t indexes arrivals, 1-based):

    gamma(j)            normalized non-increasing "reward schedule"
    W_1   = w0
    W_t   = w0 + sum_{k: tau_k < t} gamma(t - tau_k) * (b - alpha_{tau_k})  (t>=2)
    alpha_t = gamma(t) * W_t
    reject H_t iff p_t <= alpha_t

where tau_k are the past REJECTION times, alpha_{tau_k} is the threshold that
was SPENT on rejection k, and b = alpha - w0 is the constant payoff every
rejection earns. The net reward of rejection k is therefore (b - alpha_tau_k):
it earns b and gets back the pre-committed level alpha_tau_k that it consumed.

Note the level returned is alpha_tau_k, NOT p_tau_k. This matters two ways:

* It is the canonical LORD++ wealth recursion on which the FDR guarantee rests.
* alpha_tau_k was itself frozen BEFORE p_tau_k was observed, so the whole wealth
  process is a function of pre-committed quantities only -- an even stronger
  causal statement. (Using p_tau_k instead would be slightly more permissive and
  is not the rule whose guarantee is claimed.)

Crucial causality property: alpha_t depends ONLY on times 1..t-1. p_t is
observed strictly after alpha_t has been computed and committed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .errors import (
    ComputationError,
    InputValidationError,
    ResourceExhaustedError,
)

# --------------------------------------------------------------------------- #
# Frozen contract
# --------------------------------------------------------------------------- #

CONTRACT_VERSION = "lordpp-v1"

# Target online false discovery rate.
DEFAULT_ALPHA = 0.05

# Initial wealth. LORD requires w0 in (0, alpha]; alpha/2? we freeze 0.9*alpha
# so early thresholds are usable for teaching. b = alpha - w0 = 0.005.
DEFAULT_W0_FRACTION = 0.9

# Reward schedule: r(j) = C * log(max(j,2)) / max(j,2), normalized so that
# sum_{j=1..H} gamma(j) = 1 over a declared finite horizon H.
SCHEDULE_C = 0.0722  # frozen constant from the LORD literature scale
SCHEDULE_HORIZON = 1000

# A p-value smaller than this in magnitude but actually <= 0 or NaN is rejected
# at validation, never silently clamped.
P_EPS = 1e-300

ALLOWED_ALPHA_RANGE = (1e-6, 0.5)
MAX_HORIZON = 100_000


# --------------------------------------------------------------------------- #
# Configuration (frozen per run)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LordConfig:
    """Frozen run parameters. Validated once; never mutated afterwards."""

    alpha: float = DEFAULT_ALPHA
    w0: float = DEFAULT_ALPHA * DEFAULT_W0_FRACTION
    horizon: int = SCHEDULE_HORIZON
    contract_version: str = CONTRACT_VERSION

    @property
    def payoff(self) -> float:
        """b = alpha - w0, the constant reward of every rejection."""
        return self.alpha - self.w0

    @classmethod
    def create(
        cls,
        alpha: float = DEFAULT_ALPHA,
        w0: float | None = None,
        horizon: int = SCHEDULE_HORIZON,
    ) -> "LordConfig":
        a = _finite_float("alpha", alpha)
        lo, hi = ALLOWED_ALPHA_RANGE
        if not (lo <= a <= hi):
            raise InputValidationError(
                f"alpha out of allowed range [{lo}, {hi}]",
                details={"field": "alpha", "value": a},
            )
        if not isinstance(horizon, int) or isinstance(horizon, bool):
            raise InputValidationError(
                "horizon must be an integer", details={"field": "horizon"}
            )
        if not (1 <= horizon <= MAX_HORIZON):
            raise InputValidationError(
                f"horizon must be in [1, {MAX_HORIZON}]",
                details={"field": "horizon", "value": horizon},
            )
        if w0 is None:
            w = a * DEFAULT_W0_FRACTION
        else:
            w = _finite_float("w0", w0)
        if not (0.0 < w <= a):
            raise InputValidationError(
                "w0 must satisfy 0 < w0 <= alpha",
                details={"field": "w0", "value": w, "alpha": a},
            )
        return cls(alpha=a, w0=w, horizon=horizon)


def _finite_float(field: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputValidationError(
            f"{field} must be a number", details={"field": field}
        )
    fv = float(value)
    if not math.isfinite(fv):
        raise InputValidationError(
            f"{field} must be finite", details={"field": field, "value": str(value)}
        )
    return fv


def validate_pvalue(p: object) -> float:
    """Validate a p-value at the system boundary.

    Invalid p-values are REJECTED (never silently fixed): non-numeric, NaN,
    infinite or outside (0, 1]. Note p == 0 is invalid under this contract
    (a valid continuous p-value is in (0, 1]); callers needing exact zero must
    pre-truncate themselves -- the service will not do it for them.
    """
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise InputValidationError(
            "p-value must be a number", details={"field": "p_value"}
        )
    fp = float(p)
    if math.isnan(fp) or math.isinf(fp):
        raise InputValidationError(
            "p-value must be finite", details={"field": "p_value"}
        )
    if not (0.0 < fp <= 1.0):
        raise InputValidationError(
            "p-value must lie in (0, 1]", details={"field": "p_value", "value": fp}
        )
    return fp


# --------------------------------------------------------------------------- #
# Reward schedule
# --------------------------------------------------------------------------- #


def _raw_gamma(j: int) -> float:
    j = max(int(j), 2)
    return SCHEDULE_C * math.log(j) / j


def normalized_schedule(horizon: int) -> np.ndarray:
    """Return gamma(1..horizon), normalized to sum exactly (to fp precision) 1.

    The normalization constant S_H = sum r(j) is computed with math.fsum for a
    faithful, reproducible value. Entries beyond ``horizon`` are treated as
    zero: submitting past the horizon is a RESOURCE_EXHAUSTED condition.
    """
    if not (1 <= horizon <= MAX_HORIZON):
        raise InputValidationError(
            f"horizon must be in [1, {MAX_HORIZON}]",
            details={"field": "horizon", "value": horizon},
        )
    raw = np.fromiter((_raw_gamma(j) for j in range(1, horizon + 1)), dtype=np.float64)
    total = math.fsum(raw.tolist())
    if not (total > 0.0) or not math.isfinite(total):
        raise ComputationError(
            "schedule normalizing constant is non-finite/non-positive",
            details={"horizon": horizon, "S": total},
        )
    return raw / total


# --------------------------------------------------------------------------- #
# LORD++ estimation kernel
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LordStep:
    """Result of one decision step. All quantities are explanatory/diagnostic."""

    index: int
    wealth_before: float       # W_t, computed from past rejections only
    threshold: float           # alpha_t = gamma(t) * W_t
    p_value: float             # the p-value observed AFTER the threshold
    rejected: bool
    gamma_t: float
    payoff: float              # b
    wealth_after: float        # W_{t+1}-equivalent bookkeeping snapshot
    n_past_rejections: int


@dataclass
class LordState:
    """Mutable accumulator. Only :meth:`step` may advance it.

    Past rejections are stored as ``(time, spent_threshold)`` pairs -- the
    level alpha_tau that was frozen and spent at each rejection. Wealth is
    recomputed from that history each step; there is no separately mutated
    "spent budget" counter, which makes the causal property auditable. The
    observed p-values are deliberately NOT part of the wealth recursion.
    """

    config: LordConfig
    schedule: np.ndarray
    rejection_times: list[int]
    rejection_spent: list[float]
    steps_taken: int = 0

    @classmethod
    def initialize(cls, config: LordConfig) -> "LordState":
        return cls(
            config=config,
            schedule=normalized_schedule(config.horizon),
            rejection_times=[],
            rejection_spent=[],
        )

    @property
    def wealth(self) -> float:
        """Current wealth W_{t+1} given the committed rejection history."""
        return _wealth(
            self.config.w0,
            self.config.payoff,
            self.schedule,
            self.rejection_times,
            self.rejection_spent,
            self.steps_taken + 1,
        )

    def preview_threshold(self) -> float:
        """Threshold that WOULD be used for the next hypothesis.

        Exists so the service can expose the pre-committed alpha_t before the
        p-value for that hypothesis arrives.
        """
        t = self.steps_taken + 1
        if t > self.config.horizon:
            raise ResourceExhaustedError(
                "frozen horizon reached; no further threshold can be issued",
                details={"horizon": self.config.horizon},
            )
        w_next = _wealth(
            self.config.w0,
            self.config.payoff,
            self.schedule,
            self.rejection_times,
            self.rejection_spent,
            t,
        )
        return _finite_threshold(float(self.schedule[t - 1]) * w_next, t)

    def step(self, p_value: float) -> LordStep:
        """Commit one decision. Validates p-value; never mutates on bad input."""
        p = validate_pvalue(p_value)
        t = self.steps_taken + 1
        if t > self.config.horizon:
            raise ResourceExhaustedError(
                "frozen horizon reached; no further decisions can be made",
                details={"horizon": self.config.horizon, "steps_taken": self.steps_taken},
            )
        w_before = _wealth(
            self.config.w0,
            self.config.payoff,
            self.schedule,
            self.rejection_times,
            self.rejection_spent,
            t,
        )
        g_t = float(self.schedule[t - 1])
        threshold = _finite_threshold(g_t * w_before, t)
        rejected = p <= threshold
        # On rejection the level SPENT (the pre-committed alpha_t, not p_t)
        # enters the wealth recursion starting at W_{t+1}. A kept hypothesis
        # changes no state at all.
        new_times = self.rejection_times + [t] if rejected else self.rejection_times
        new_spent = (
            self.rejection_spent + [threshold] if rejected else self.rejection_spent
        )
        w_after = _wealth(
            self.config.w0,
            self.config.payoff,
            self.schedule,
            new_times,
            new_spent,
            t + 1,
        )
        # Commit only after every computation above succeeded.
        self.rejection_times = new_times
        self.rejection_spent = new_spent
        self.steps_taken = t
        return LordStep(
            index=t,
            wealth_before=w_before,
            threshold=threshold,
            p_value=p,
            rejected=rejected,
            gamma_t=g_t,
            payoff=self.config.payoff,
            wealth_after=w_after,
            n_past_rejections=len(new_times) - (1 if rejected else 0),
        )


def _wealth(
    w0: float,
    payoff: float,
    schedule: np.ndarray,
    rej_times: list[int],
    rej_spent: list[float],
    t: int,
) -> float:
    """W_t from PAST rejections only (tau_k < t).

    Each past rejection contributes gamma(t - tau_k) * (b - alpha_{tau_k}),
    where alpha_{tau_k} is the level it spent -- a quantity fixed before that
    p-value was ever observed.
    """
    terms = [
        float(schedule[(t - tau) - 1]) * (payoff - spent)
        for tau, spent in zip(rej_times, rej_spent)
        if tau < t and 1 <= (t - tau) <= schedule.shape[0]
    ]
    w = w0 + math.fsum(terms)
    if not math.isfinite(w):
        raise ComputationError(
            "non-finite wealth computed", details={"t": t, "wealth": str(w)}
        )
    if w < 0.0:
        # LORD++ wealth can in principle be non-negative under the guarantee; a
        # negative value signals numerical trouble worth surfacing.
        raise ComputationError(
            "negative wealth encountered", details={"t": t, "wealth": w}
        )
    return w


def _finite_threshold(value: float, t: int) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise ComputationError(
            "non-finite/negative threshold",
            details={"t": t, "threshold": value},
        )
    return float(value)
