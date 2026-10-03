"""Hard seed constraints via a provably sufficient big-M weight.

A seed "pixel p must take label L" is encoded by adding a weight ``M`` to
the opposite unary cost. ``M`` is sufficient iff it exceeds the largest
energy any seed-respecting labeling could possibly attain, so flipping one
seeded pixel is always worse than any feasible alternative. We use

    M = sum_p max(D_p(0), D_p(1)) + sum_(p,q) max(V_pq) + 1

which strictly upper-bounds the energy of any labeling, hence is sufficient.

Overflow guard: capacities live in float64. We require the total augmented
capacity (sum of all unaries + pairwise caps + n_seeds * M) to stay below
2**52 so that flow accumulation keeps exact integer-like behaviour well
inside the 53-bit mantissa. Inputs that would exceed this are rejected as
resource exhaustion, not silently truncated.
"""

from __future__ import annotations

import numpy as np

from .errors import ResourceExhaustedError, StateConflictError
from .models import EnergySpec

SAFE_CAPACITY_BOUND = 2.0**52


def sufficient_seed_weight(spec: EnergySpec) -> float:
    """Big-M: strict upper bound on any labeling's energy, plus one."""
    unary_bound = float(
        np.maximum(spec.unary0, spec.unary1).sum(dtype=np.float64)
    )
    pairwise_bound = sum(
        max(t.v00, t.v01, t.v10, t.v11) for t in spec.pairwise
    )
    return unary_bound + pairwise_bound + 1.0


def apply_seeds(
    spec: EnergySpec,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return ``(unary0', unary1', M)`` with seed weights folded in.

    Raises:
        StateConflictError: a pixel is seeded as both foreground and
            background — the constraints are mutually exclusive.
        ResourceExhaustedError: the augmented capacity total would exceed
            the float64 safety bound.
    """
    fg = set(spec.seeds.foreground)
    bg = set(spec.seeds.background)
    conflicts = sorted(fg & bg)
    if conflicts:
        raise StateConflictError(
            "conflicting_seeds",
            f"{len(conflicts)} pixel(s) seeded as both foreground and "
            f"background: {conflicts[:8]}{'...' if len(conflicts) > 8 else ''}",
            details={"pixels": conflicts},
        )

    big_m = sufficient_seed_weight(spec)
    u0 = spec.unary0.astype(np.float64, copy=True)
    u1 = spec.unary1.astype(np.float64, copy=True)
    flat0 = u0.reshape(-1)
    flat1 = u1.reshape(-1)
    for idx in fg:  # foreground forced: forbid label 0
        flat0[idx] += big_m
    for idx in bg:  # background forced: forbid label 1
        flat1[idx] += big_m

    total_capacity = (
        float(flat0.sum(dtype=np.float64))
        + float(flat1.sum(dtype=np.float64))
        + sum(t.v01 + t.v10 - t.v00 - t.v11 for t in spec.pairwise)
    )
    if total_capacity >= SAFE_CAPACITY_BOUND:
        raise ResourceExhaustedError(
            "seed_weight_overflow",
            f"augmented capacity total {total_capacity:.3e} exceeds the "
            f"float64 safety bound {SAFE_CAPACITY_BOUND:.3e}; seed weights "
            "could lose precision",
            details={"total_capacity": total_capacity},
        )
    return u0, u1, big_m
