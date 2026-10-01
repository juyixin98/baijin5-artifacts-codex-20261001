"""Reproducible Monte Carlo experiments with an INDEPENDENT LORD++ implementation.

The reference implementation in this module is deliberately written separately
from :mod:`app.statistics`: it uses plain Python floats/``math.fsum`` and its
own schedule/loop rather than importing the kernel under test. The test suite
cross-checks the two implementations against each other; empirical FDR numbers
come from THIS independent code path, so the service is not graded by itself.

Only statistical facts live here:

* synthetic p-value streams with recorded truth labels (fixed seeds),
* an online LORD++ loop,
* per-run false discoveries / power,
* aggregate Monte Carlo estimate with standard error.

Nothing here touches SQLite or HTTP.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.stats import norm

from .statistics import (
    DEFAULT_ALPHA,
    DEFAULT_W0_FRACTION,
    SCHEDULE_C,
    SCHEDULE_HORIZON,
    normalized_schedule,
)

# Independent, explicit RNG construction for reproducibility.
DEFAULT_SEED = 20260927


# --------------------------------------------------------------------------- #
# Independent LORD++ reference loop (stdlib math; does NOT import the kernel)
# --------------------------------------------------------------------------- #


def independent_gamma(j: int, horizon: int) -> float:
    """Reference gamma using an independently accumulated normalization sum."""
    if not 1 <= j <= horizon:
        return 0.0
    total = 0.0
    for i in range(1, horizon + 1):
        ii = i if i >= 2 else 2
        total += SCHEDULE_C * math.log(ii) / ii
    jj = j if j >= 2 else 2
    return (SCHEDULE_C * math.log(jj) / jj) / total


def run_lordpp_reference(
    p_values: list[float],
    alpha: float = DEFAULT_ALPHA,
    w0: float | None = None,
    horizon: int = SCHEDULE_HORIZON,
) -> dict[str, Any]:
    """Standalone canonical LORD++ decision loop.

    Wealth recursion W_t = w0 + sum gamma(t-tau_k)*(b - alpha_{tau_k}); the
    returned level alpha_{tau_k} is the threshold rejection k spent (frozen
    before its p-value was observed), never the p-value itself.

    Mirrors the frozen contract but shares no code with the production kernel.
    Returns full trajectories so tests can assert exact intermediate values.
    """
    if w0 is None:
        w0 = alpha * DEFAULT_W0_FRACTION
    b = alpha - w0
    n = len(p_values)
    if n > horizon:
        raise ValueError("stream longer than frozen horizon")

    # Independent schedule accumulation.
    s_total = math.fsum(
        SCHEDULE_C * math.log(max(i, 2)) / max(i, 2) for i in range(1, horizon + 1)
    )

    def gamma(j: int) -> float:
        rj = SCHEDULE_C * math.log(max(j, 2)) / max(j, 2)
        return rj / s_total

    tau: list[int] = []
    spent: list[float] = []  # alpha_{tau_k} spent on each rejection (pre-p-value)
    wealth: list[float] = []
    thresholds: list[float] = []
    rejected: list[bool] = []
    for t in range(1, n + 1):
        w = w0 + math.fsum(gamma(t - k) * (b - a) for k, a in zip(tau, spent))
        a_t = gamma(t) * w
        p = float(p_values[t - 1])
        decision = p <= a_t
        wealth.append(w)
        thresholds.append(a_t)
        rejected.append(decision)
        if decision:
            tau.append(t)
            spent.append(a_t)
    return {
        "wealth": wealth,
        "thresholds": thresholds,
        "rejected": rejected,
        "n_rejections": sum(rejected),
    }


# --------------------------------------------------------------------------- #
# Synthetic streams
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Stream:
    p_values: np.ndarray
    is_alternative: np.ndarray  # True = non-null ground truth
    seed: int
    n: int
    pi1: float
    effect: float
    kind: str
    assignment_seed: int

    def truth_summary(self) -> dict[str, int]:
        return {
            "n": int(self.n),
            "n_alternative": int(self.is_alternative.sum()),
            "n_null": int((~self.is_alternative).sum()),
        }


def _labels(n: int, pi1: float, rng: np.random.Generator) -> np.ndarray:
    if not 0.0 <= pi1 <= 1.0:
        raise ValueError("pi1 must be in [0,1]")
    if pi1 == 0.0:
        return np.zeros(n, dtype=bool)
    if pi1 == 1.0:
        return np.ones(n, dtype=bool)
    return rng.random(n) < pi1


def gaussian_stream(
    n: int,
    pi1: float,
    effect: float = 3.5,
    seed: int = DEFAULT_SEED,
) -> Stream:
    """Two-sided Z-test stream.

    Nulls: Z ~ N(0,1). Alternatives: Z ~ N(effect,1). Truth labels are drawn
    first with a documented child-seed, then statistics independently.
    """
    rng = np.random.default_rng(seed)
    label_rng = np.random.default_rng(seed * 7 + 11)
    is_alt = _labels(n, pi1, label_rng)
    z = rng.standard_normal(n) + np.where(is_alt, effect, 0.0)
    p = 2.0 * norm.sf(np.abs(z))
    # Guard against any exact-zero underflow (not expected at effect <= 4, but
    # an invalid p-value would violate the contract the reference also obeys).
    p = np.maximum(p, np.finfo(float).tiny)
    return Stream(
        p_values=p,
        is_alternative=is_alt,
        seed=int(seed),
        n=int(n),
        pi1=float(pi1),
        effect=float(effect),
        kind="gaussian-two-sided",
        assignment_seed=int(seed * 7 + 11),
    )


def null_stream(n: int, seed: int = DEFAULT_SEED) -> Stream:
    """Pure-null Uniform(0,1) stream: every rejection is a false discovery."""
    rng = np.random.default_rng(seed)
    p = rng.random(n)
    # Avoid exact 0/1 boundaries defensively (rng.random returns [0,1)).
    p = np.minimum(np.maximum(p, np.finfo(float).tiny), 1.0)
    return Stream(
        p_values=p,
        is_alternative=np.zeros(n, dtype=bool),
        seed=int(seed),
        n=int(n),
        pi1=0.0,
        effect=0.0,
        kind="uniform-null",
        assignment_seed=int(seed),
    )


def fixed_signal_stream(
    n: int,
    alt_positions: list[int],
    seed: int = DEFAULT_SEED,
    alt_p: float = 1e-10,
) -> Stream:
    """Mixed stream with deterministic tiny alternative p-values.

    Null p-values are Uniform from a fixed seed; positions are 0-based. Useful
    for exact hand-checkable mixed-flow assertions.
    """
    rng = np.random.default_rng(seed)
    p = rng.random(n)
    is_alt = np.zeros(n, dtype=bool)
    for pos in alt_positions:
        if not 0 <= pos < n:
            raise ValueError("alternative position out of range")
        is_alt[pos] = True
        p[pos] = alt_p
    p = np.minimum(np.maximum(p, np.finfo(float).tiny), 1.0)
    return Stream(
        p_values=p,
        is_alternative=is_alt,
        seed=int(seed),
        n=int(n),
        pi1=float(len(alt_positions) / n),
        effect=float("inf"),
        kind="fixed-signal",
        assignment_seed=int(seed),
    )


# --------------------------------------------------------------------------- #
# Experiment aggregation
# --------------------------------------------------------------------------- #


@dataclass
class Replication:
    run_no: int
    seed: int
    n: int
    n_alternative: int
    rejections: int
    false_discoveries: int
    true_rejections: int
    fdp: float                 # V / max(R, 1)
    marginal_power: float      # true rejections / n_alternative (0 if no alt)


@dataclass
class ExperimentResult:
    name: str
    contract_schedule_c: float
    alpha: float
    w0: float
    horizon: int
    stream_kind: str
    n_per_run: int
    pi1: float
    effect: float
    base_seed: int
    n_replications: int
    replications: list[Replication] = field(default_factory=list)
    fdr_estimate: float = 0.0
    fdp_std: float = 0.0
    fdr_se: float = 0.0
    ci95_low: float = 0.0
    ci95_high: float = 0.0
    marginal_power: float = 0.0
    any_rejection_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def evaluate_stream(
    stream: Stream,
    run_no: int,
    alpha: float = DEFAULT_ALPHA,
    w0: float | None = None,
    horizon: int = SCHEDULE_HORIZON,
) -> Replication:
    if w0 is None:
        w0 = alpha * DEFAULT_W0_FRACTION
    ref = run_lordpp_reference(stream.p_values.tolist(), alpha=alpha, w0=w0, horizon=horizon)
    rejected = np.asarray(ref["rejected"], dtype=bool)
    r = int(rejected.sum())
    false_v = int((rejected & ~stream.is_alternative).sum())
    true_s = int((rejected & stream.is_alternative).sum())
    n_alt = int(stream.is_alternative.sum())
    return Replication(
        run_no=run_no,
        seed=stream.seed,
        n=stream.n,
        n_alternative=n_alt,
        rejections=r,
        false_discoveries=false_v,
        true_rejections=true_s,
        fdp=false_v / max(r, 1),
        marginal_power=(true_s / n_alt) if n_alt else 0.0,
    )


def run_experiment(
    name: str,
    n_per_run: int,
    n_replications: int,
    pi1: float,
    effect: float = 3.5,
    base_seed: int = DEFAULT_SEED,
    alpha: float = DEFAULT_ALPHA,
    w0: float | None = None,
    horizon: int = SCHEDULE_HORIZON,
    stream_kind: str = "gaussian-two-sided",
) -> ExperimentResult:
    """Repeat the online decision process over independent seeded streams."""
    if w0 is None:
        w0 = alpha * DEFAULT_W0_FRACTION
    result = ExperimentResult(
        name=name,
        contract_schedule_c=SCHEDULE_C,
        alpha=alpha,
        w0=w0,
        horizon=horizon,
        stream_kind=stream_kind,
        n_per_run=n_per_run,
        pi1=pi1,
        effect=effect,
        base_seed=base_seed,
        n_replications=n_replications,
    )
    for i in range(n_replications):
        seed = base_seed + i * 1009  # fixed stride; every run id is recorded
        if stream_kind == "gaussian-two-sided":
            stream = gaussian_stream(n_per_run, pi1, effect=effect, seed=seed)
        elif stream_kind == "uniform-null":
            stream = null_stream(n_per_run, seed=seed)
        else:
            raise ValueError(f"unknown stream kind: {stream_kind}")
        result.replications.append(
            evaluate_stream(stream, run_no=i + 1, alpha=alpha, w0=w0, horizon=horizon)
        )
    fdps = np.array([rep.fdp for rep in result.replications], dtype=float)
    total_true = sum(r.true_rejections for r in result.replications)
    total_alt = sum(r.n_alternative for r in result.replications)
    result.fdr_estimate = float(fdps.mean())
    result.fdp_std = float(fdps.std(ddof=1)) if n_replications > 1 else 0.0
    result.fdr_se = result.fdp_std / math.sqrt(n_replications)
    result.ci95_low = max(0.0, result.fdr_estimate - 1.96 * result.fdr_se)
    result.ci95_high = result.fdr_estimate + 1.96 * result.fdr_se
    result.marginal_power = (total_true / total_alt) if total_alt else 0.0
    result.any_rejection_rate = float(
        np.mean([r.rejections > 0 for r in result.replications])
    )
    return result


# Re-export for callers that prefer one symbol.
__all__ = [
    "Stream",
    "Replication",
    "ExperimentResult",
    "independent_gamma",
    "run_lordpp_reference",
    "gaussian_stream",
    "null_stream",
    "fixed_signal_stream",
    "evaluate_stream",
    "run_experiment",
    "normalized_schedule",
]
