"""Paired randomization design.

Contract point 1: the randomization set is generated *strictly by pair*.
For ``n`` matched pairs each assignment independently chooses which member
of a pair receives treatment, so the set has exactly ``2**n`` assignments.
Under every assignment each pair keeps its own two outcomes -- outcomes are
never permuted across pair boundaries (no whole-sample shuffle).
"""

from __future__ import annotations

from typing import Iterator, List, Sequence, Tuple

import numpy as np

from .contracts import PairDesign

MIN_PAIRS = 2


def validate_observations(treated: Sequence[float], control: Sequence[float]) -> PairDesign:
    """Build a :class:`PairDesign`, failing fast on malformed input."""
    t = list(treated)
    c = list(control)
    if len(t) != len(c):
        raise ValueError("treated and control must have the same length (one value per pair)")
    if len(t) < MIN_PAIRS:
        raise ValueError(f"a paired design needs at least {MIN_PAIRS} pairs, got {len(t)}")
    arr = np.asarray(t + c, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError("all outcomes must be finite real numbers")
    return PairDesign(treated=tuple(t), control=tuple(c))


def sign_vectors(n_pairs: int) -> np.ndarray:
    """All ``2**n`` within-pair flip indicators, shape ``(2**n, n)`` in {0,1}.

    Row ``a`` is the binary image of ``a``; column ``i`` is pair ``i``.
    This is the complete paired randomization set -- one independent bit per
    pair, never a permutation over individuals.
    """
    if n_pairs < 0:
        raise ValueError("n_pairs must be non-negative")
    size = 2**n_pairs
    bits = ((np.arange(size, dtype=np.int64)[:, None] >> np.arange(n_pairs, dtype=np.int64)) & 1)
    return bits.astype(np.int8)


def assignment_differences(design: PairDesign) -> np.ndarray:
    """Signed pair-differences for the whole randomization set.

    Returns an ``(2**n, n)`` array; entry ``[a, i]`` is ``+d_i`` when pair
    ``i`` keeps its observed orientation under assignment ``a`` and ``-d_i``
    when that pair alone is flipped.  Columns other than ``i`` cannot affect
    row ``i``'s pair, which is what makes the design strictly paired.
    """
    d = np.asarray(design.differences, dtype=float)
    flips = sign_vectors(design.n_pairs)
    return d[None, :] * (1 - 2 * flips.astype(float))


def iter_assignments(design: PairDesign) -> Iterator[Tuple[Tuple[float, ...], Tuple[float, ...]]]:
    """Yield ``(treated_outcomes, control_outcomes)`` for every assignment.

    Each pair's two values are merely swapped in place -- useful for tests
    that assert no value ever crosses pair boundaries.
    """
    t = design.treated
    c = design.control
    for bits in sign_vectors(design.n_pairs):
        nt: List[float] = []
        nc: List[float] = []
        for i, b in enumerate(bits):
            if b:
                nt.append(c[i])
                nc.append(t[i])
            else:
                nt.append(t[i])
                nc.append(c[i])
        yield tuple(nt), tuple(nc)
