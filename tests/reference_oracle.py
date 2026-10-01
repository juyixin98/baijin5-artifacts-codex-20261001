"""Independent reference oracle for paired randomization tests.

This oracle is intentionally written on a *different implementation path*
from the production kernel (``app.stats.estimator``):

* exact rational arithmetic via :class:`fractions.Fraction` (no floating
  point, no numpy);
* explicit bit-mask enumeration with hand-rolled loops (no vectorization);
* acceptance-set reconstruction from the full ordered set of analytic
  breakpoints, rather than grid scan or bisection.

Tests use it to derive expected answers; the production code never calls
this module and this module never imports the application package.
"""

from __future__ import annotations

import itertools
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

# A half-open/closed piece: (lower, lower_closed, upper, upper_closed).
# ``None`` bound means -oo / +oo.
Piece = Tuple[Optional[Fraction], bool, Optional[Fraction], bool]


def _forms(d: Sequence[Fraction]) -> List[Tuple[Fraction, int]]:
    """All (B_a, k_a) with S_a(tau) = B_a - tau*k_a, over 2**n flips."""
    n = len(d)
    forms: List[Tuple[Fraction, int]] = []
    for mask in range(2**n):
        b = Fraction(0)
        k = 0
        for i, di in enumerate(d):
            if (mask >> i) & 1:  # flipped: pair i contributes -1
                b -= di
                k -= 1
            else:  # observed orientation: +1
                b += di
                k += 1
        forms.append((b, k))
    return forms


def _p_abs(d: Sequence[Fraction], tau: Fraction) -> Tuple[int, int]:
    forms = _forms(d)
    n = len(d)
    obs = sum(d) - n * tau
    extreme = 0
    for b, k in forms:
        if abs(b - tau * k) >= abs(obs):
            extreme += 1
    return extreme, len(forms)


def _p_prob(d: Sequence[Fraction], tau: Fraction) -> Tuple[int, int]:
    """Probability-ordering two-sided p.

    Values of the statistic are grouped by *exact* equality; a value is as
    or more extreme as the observed value iff its null probability mass is
    no larger than the observed value's mass.
    """
    forms = _forms(d)
    n = len(d)
    masses: dict[Fraction, int] = {}
    for b, k in forms:
        v = b - tau * k
        masses[v] = masses.get(v, 0) + 1
    obs = sum(d) - n * tau
    p_obs = masses[obs]
    extreme = sum(m for m in masses.values() if m <= p_obs)
    return extreme, len(forms)


_P_FUNS = {"two_sided_abs": _p_abs, "two_sided_prob": _p_prob}


def exact_p_value(d: Sequence[float], tau: float, method: str) -> Fraction:
    fd = [Fraction(str(x)) for x in d]
    ft = Fraction(str(tau))
    extreme, total = _P_FUNS[method](fd, ft)
    return Fraction(extreme, total)


def _breakpoints(d: Sequence[Fraction], method: str) -> List[Fraction]:
    """All tau at which the p-value expression can change, exactly."""
    n = len(d)
    total = sum(d)
    forms = _forms(d)
    roots = set()
    # Every assignment can cross the observed statistic (|.| boundary or
    # observed-value identity change).
    for b, k in forms:
        if k != n:
            roots.add((b - total) / (k - n))
    if method == "two_sided_prob":
        # Ordering changes whenever two assignment values cross.
        for (b1, k1), (b2, k2) in itertools.combinations(forms, 2):
            if k1 != k2:
                roots.add(Fraction(b1 - b2, k1 - k2))
    return sorted(roots)


def _accepted_pieces(d: Sequence[Fraction], alpha: Fraction, method: str) -> List[Piece]:
    """Reconstruct the acceptance set as maximal connected components.

    The real line is partitioned, in order, into strictly alternating cells::

        G0, {r1}, G1, {r2}, ..., {rm}, Gm

    where the ``r`` are the breakpoints and the ``G`` are open gaps (the two
    outer gaps are unbounded).  The p-value expression is constant on each
    gap and evaluated separately at each point.  A maximal run of accepted
    adjacent cells is one interval; its lower/upper endpoint is closed
    exactly when the run starts/ends on an accepted point cell.
    """
    p_fun = _P_FUNS[method]
    roots = _breakpoints(d, method)
    total = 2 ** len(d)

    def kept(tau: Fraction) -> bool:
        extreme, _ = p_fun(d, tau)
        return Fraction(extreme, total) > alpha

    # Ordered cells: kind "gap" -> (lo, hi) with None = unbounded;
    # kind "point" -> (r, r).
    cells: List[Tuple[str, Optional[Fraction], Optional[Fraction]]] = []
    if not roots:
        cells.append(("gap", None, None))
    else:
        cells.append(("gap", None, roots[0]))
        for i, r in enumerate(roots):
            cells.append(("point", r, r))
            hi = roots[i + 1] if i + 1 < len(roots) else None
            cells.append(("gap", r, hi))

    pieces: List[Piece] = []
    run_lo: Optional[Fraction] = None
    run_start_closed = False
    run_hi: Optional[Fraction] = None
    run_end_closed = False
    in_run = False
    for kind, lo, hi in cells:
        probe = _gap_midpoint(lo, hi, roots) if kind == "gap" else lo
        accepted = kept(probe)  # type: ignore[arg-type]
        if accepted:
            if not in_run:
                run_lo = lo
                run_start_closed = kind == "point"
                in_run = True
            run_hi = hi
            run_end_closed = kind == "point"
        elif in_run:
            pieces.append((run_lo, run_start_closed, run_hi, run_end_closed))
            in_run = False
    if in_run:
        pieces.append((run_lo, run_start_closed, run_hi, run_end_closed))
    return pieces


def _gap_midpoint(lo: Optional[Fraction], hi: Optional[Fraction], roots: Sequence[Fraction]) -> Fraction:
    if lo is None and hi is not None:
        return hi - 1
    if hi is None and lo is not None:
        return lo + 1
    if lo is None and hi is None:
        return Fraction(0)
    return (lo + hi) / 2  # type: ignore[operator]


def exact_acceptance_set(d: Sequence[float], alpha: float, method: str) -> List[Piece]:
    fd = [Fraction(str(x)) for x in d]
    fa = Fraction(str(alpha))
    return _accepted_pieces(fd, fa, method)


def piece_as_floats(piece: Piece) -> Tuple[Optional[float], bool, Optional[float], bool]:
    lo, lc, hi, hc = piece
    return (
        None if lo is None else float(lo),
        lc,
        None if hi is None else float(hi),
        hc,
    )
