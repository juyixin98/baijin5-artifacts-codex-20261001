"""Local synthetic p-value fixtures.

All streams are generated locally with NumPy generators seeded by the caller.
A stream fixes the hypothesis order and labels every p-value with the truth
(``is_null``) so experiments can measure false discoveries against ground
truth — the service API itself never sees labels.

Generators
----------
* null stream:   every p-value i.i.d. Uniform[0,1] (the complete-null case).
* mixed stream:  null p-values Uniform[0,1]; non-null p-values from a
                 Beta(a, 1) distribution (stochastic small), i.e. a valid
                 alternative p-value distribution.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True, slots=True)
class PValueStream:
    hypothesis_ids: list[str]
    p_values: list[float]
    is_null: list[bool]
    seed: int
    kind: str
    params: dict

    @property
    def n_tests(self) -> int:
        return len(self.p_values)

    @property
    def n_null(self) -> int:
        return sum(self.is_null)

    @property
    def n_nonnull(self) -> int:
        return self.n_tests - self.n_null


def null_stream(n_tests: int, seed: int, prefix: str = "H") -> PValueStream:
    """Complete-null stream: i.i.d. uniform p-values, labels all True."""
    if n_tests < 0:
        raise ValueError("n_tests must be non-negative")
    rng = np.random.default_rng(seed)
    p = rng.random(n_tests)
    ids = [f"{prefix}{i}" for i in range(1, n_tests + 1)]
    return PValueStream(
        hypothesis_ids=ids,
        p_values=[float(x) for x in p],
        is_null=[True] * n_tests,
        seed=seed,
        kind="null_uniform",
        params={"n_tests": n_tests},
    )


def mixed_stream(
    n_tests: int,
    nonnull_fraction: float,
    seed: int,
    *,
    beta_a: float = 0.05,
    block: bool = False,
    prefix: str = "H",
) -> PValueStream:
    """Mixed stream with a fixed fraction of Beta(a,1) non-null p-values.

    ``block=True`` places all non-null hypotheses first (dense-early signal);
    the default interleaves by seeded Bernoulli draws.  Ground-truth labels
    are fixed before any p-value is generated.
    """
    if not 0.0 <= nonnull_fraction <= 1.0:
        raise ValueError("nonnull_fraction must be in [0, 1]")
    if beta_a <= 0.0:
        raise ValueError("beta_a must be positive")
    rng = np.random.default_rng(seed)
    n_nonnull = int(round(nonnull_fraction * n_tests))
    is_null = [True] * n_tests
    if block:
        positions = list(range(n_nonnull))
    else:
        positions = sorted(
            rng.choice(n_tests, size=n_nonnull, replace=False).tolist()
        ) if n_nonnull > 0 else []
    for pos in positions:
        is_null[pos] = False
    p_values: list[float] = []
    for null_flag in is_null:
        if null_flag:
            p_values.append(float(rng.random()))
        else:
            p_values.append(float(stats.beta.rvs(beta_a, 1.0, random_state=rng)))
    ids = [f"{prefix}{i}" for i in range(1, n_tests + 1)]
    return PValueStream(
        hypothesis_ids=ids,
        p_values=p_values,
        is_null=is_null,
        seed=seed,
        kind="mixed_uniform_beta",
        params={
            "n_tests": n_tests,
            "nonnull_fraction": nonnull_fraction,
            "beta_a": beta_a,
            "block": block,
        },
    )
