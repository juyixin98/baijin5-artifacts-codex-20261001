"""Bootstrap confidence intervals by resampling comparable sites.

Algorithm (fixed, documented so results are reproducible and can be
re-implemented independently):

1. Let `outcomes` be the per-site category codes (0=match, 1=transition,
   2=transversion) of the n comparable sites, in alignment order.
2. rng = numpy.random.Generator(numpy.random.PCG64(seed)) - the seed is
   explicit and recorded; nothing here reads global random state.
3. For each of B replicates: draw n indices with replacement via
   rng.integers(0, n, size=n), aggregate the resampled outcomes into
   SiteCounts, and re-estimate the distance under the chosen model.
4. Replicates whose estimate falls outside the model's valid domain are
   counted (`n_saturated`) and excluded from the percentile computation -
   never clamped, never abs()'d.
5. CI = numpy.percentile(estimable values,
   [100*(1-confidence)/2, 100*(1+confidence)/2]) with numpy's default
   linear interpolation. If no replicate is estimable, the interval is
   reported as not estimable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

from .errors import ResourceExhaustedError
from .models import Model, SiteCounts, EstimateStatus, estimate_distance

MAX_BOOTSTRAP_REPLICATES = 100_000


class BootstrapStatus(str, enum.Enum):
    OK = "ok"
    NOT_ESTIMABLE = "not_estimable"  # every replicate saturated / no sites


@dataclass(frozen=True)
class BootstrapResult:
    status: BootstrapStatus
    ci_low: float | None
    ci_high: float | None
    confidence: float
    replicates: int
    n_saturated: int
    seed: int


def bootstrap_ci(
    outcomes: list[int],
    model: Model,
    replicates: int,
    confidence: float,
    seed: int,
) -> BootstrapResult:
    if replicates > MAX_BOOTSTRAP_REPLICATES:
        raise ResourceExhaustedError(
            "too_many_bootstrap_replicates",
            f"replicates {replicates} exceeds limit {MAX_BOOTSTRAP_REPLICATES}",
            {"replicates": replicates, "limit": MAX_BOOTSTRAP_REPLICATES},
        )

    n = len(outcomes)
    if n == 0:
        return BootstrapResult(BootstrapStatus.NOT_ESTIMABLE, None, None,
                               confidence, replicates, 0, seed)

    arr = np.asarray(outcomes, dtype=np.int64)
    rng = np.random.Generator(np.random.PCG64(seed))

    values: list[float] = []
    n_saturated = 0
    for _ in range(replicates):
        idx = rng.integers(0, n, size=n)
        sample = arr[idx]
        counts = SiteCounts(
            n_valid=n,
            n_match=int((sample == 0).sum()),
            n_transition=int((sample == 1).sum()),
            n_transversion=int((sample == 2).sum()),
        )
        est = estimate_distance(counts, model)
        if est.status is EstimateStatus.OK and est.distance is not None:
            values.append(est.distance)
        else:
            n_saturated += 1

    if not values:
        return BootstrapResult(BootstrapStatus.NOT_ESTIMABLE, None, None,
                               confidence, replicates, n_saturated, seed)

    low_q = 100.0 * (1.0 - confidence) / 2.0
    high_q = 100.0 * (1.0 + confidence) / 2.0
    ci_low, ci_high = np.percentile(np.asarray(values), [low_q, high_q])
    return BootstrapResult(
        status=BootstrapStatus.OK,
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        confidence=confidence,
        replicates=replicates,
        n_saturated=n_saturated,
        seed=seed,
    )
