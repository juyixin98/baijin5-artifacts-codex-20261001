"""Core statistical engine.

Three responsibilities, all using the *same* two-sided statistic defined in
:mod:`app.core.contract`:

1. Exact paired randomization p-value by enumerating all ``2^n`` sign-flip
   assignments (never a shuffle across pairs).
2. Exact inversion for the constant-effect confidence set: a sweep over the
   breakpoints of the counting function, so the (possibly disconnected)
   acceptance set is recovered with its real endpoints instead of being forced
   onto one interval or one grid.
3. Monte-Carlo approximation with a reported standard error / error band, used
   when ``2^n`` exceeds the configured combinatorial budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
from scipy import stats

from app.core.contract import n_assignments, statistic_abs_signed_sum
from app.core.intervals import Interval, IntervalSet

# Enumerate sign matrices in chunks so peak memory stays bounded even when the
# budget allows large n.  2**16 int8 signs is ~2 MiB per chunk.
_ENUM_CHUNK = 65_536


# ---------------------------------------------------------------------------
# Randomization-set enumeration
# ---------------------------------------------------------------------------

def iter_sign_vector_chunks(n_pairs: int,
                            chunk_size: int = _ENUM_CHUNK
                            ) -> Iterator[np.ndarray]:
    """Yield all ``2^n`` paired-assignment sign vectors, chunked.

    Row ``k`` encodes integer ``k`` in ``n`` bits as +1/-1 signs. Bit ``i``
    is the independent treatment coin of pair ``i``. This enumerates the
    paired randomization set exactly; there is no cross-pair permutation.
    """
    total = n_assignments(n_pairs)
    bit_index = np.arange(n_pairs, dtype=np.int64)
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        k = np.arange(start, end, dtype=np.int64)
        bits = ((k[:, None] >> bit_index) & 1).astype(np.int8)
        yield 2 * bits - 1


def enumerate_signed_sums(differences: np.ndarray
                          ) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(S, B)`` over every paired assignment.

    ``S_s = sum_i s_i d_i`` is the signed sum of observed differences and
    ``B_s = sum_i s_i`` is the sign balance. For a constant effect tau the
    signed residual sum is obtained without re-enumeration::

        sum_i s_i (d_i - tau) = S_s - tau * B_s.
    """
    n = differences.size
    s_values = np.empty(n_assignments(n), dtype=np.float64)
    b_values = np.empty(s_values.size, dtype=np.int64)
    pos = 0
    for signs in iter_sign_vector_chunks(n):
        size = signs.shape[0]
        s_values[pos:pos + size] = signs.astype(np.float64) @ differences
        b_values[pos:pos + size] = signs.sum(axis=1)
        pos += size
    return s_values, b_values


# ---------------------------------------------------------------------------
# P-values
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PValueResult:
    method: str
    effect: float
    statistic_name: str
    statistic_observed: float
    p_value: float
    n_pairs: int
    n_assignments: int
    # Exact method:
    count_as_extreme: int | None = None
    # Monte-Carlo method:
    n_draws: int | None = None
    mc_standard_error: float | None = None
    mc_error_halfwidth: float | None = None
    mc_error_confidence: float | None = None
    seed: int | None = None
    diagnostic: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "effect": self.effect,
            "statistic_name": self.statistic_name,
            "statistic_observed": self.statistic_observed,
            "p_value": self.p_value,
            "n_pairs": self.n_pairs,
            "n_assignments": self.n_assignments,
            "count_as_extreme": self.count_as_extreme,
            "n_draws": self.n_draws,
            "mc_standard_error": self.mc_standard_error,
            "mc_error_halfwidth": self.mc_error_halfwidth,
            "mc_error_confidence": self.mc_error_confidence,
            "seed": self.seed,
            "diagnostic": self.diagnostic,
        }


def exact_pvalue(differences: np.ndarray, effect: float = 0.0) -> PValueResult:
    """Exact two-sided paired randomization p-value for H0: effect == tau."""
    d = np.asarray(differences, dtype=float)
    n = d.size
    s_values, b_values = enumerate_signed_sums(d)
    observed = float(statistic_abs_signed_sum(d - effect))
    null_stats = np.abs(s_values - effect * b_values)
    count = int(np.count_nonzero(null_stats >= observed - 1e-12))
    total = n_assignments(n)
    return PValueResult(
        method="exact-signflip",
        effect=float(effect),
        statistic_name="abs_signed_sum",
        statistic_observed=observed,
        p_value=count / total,
        n_pairs=n,
        n_assignments=total,
        count_as_extreme=count,
        diagnostic={
            "enumeration": "all 2**n independent within-pair sign flips",
            "ties_counted_with_observed": True,
        },
    )


def mc_pvalue(differences: np.ndarray, effect: float, n_draws: int,
              seed: int | None = None,
              error_confidence: float = 0.95) -> PValueResult:
    """Monte-Carlo randomization p-value with a binomial error band.

    Uses the standard randomization-test estimator with the observed
    assignment included: ``p = (1 + X) / (1 + M)``. The reported standard
    error treats the M simulated draws as Bernoulli(p) trials.
    """
    if not isinstance(n_draws, int) or n_draws < 1:
        raise ValueError("n_draws must be a positive integer")
    d = np.asarray(differences, dtype=float)
    n = d.size
    rng = np.random.default_rng(seed)
    residuals = d - effect
    observed = float(statistic_abs_signed_sum(residuals))

    s_draws = np.empty(n_draws, dtype=np.float64)
    b_draws = np.empty(n_draws, dtype=np.int64)
    pos = 0
    chunk = min(n_draws, 1_000_000)
    while pos < n_draws:
        m = min(chunk, n_draws - pos)
        signs = 2 * rng.integers(0, 2, size=(m, n), dtype=np.int8) - 1
        s_draws[pos:pos + m] = signs.astype(np.float64) @ d
        b_draws[pos:pos + m] = signs.sum(axis=1)
        pos += m
    null_stats = np.abs(s_draws - effect * b_draws)
    successes = int(np.count_nonzero(null_stats >= observed - 1e-12))

    p_estimate = (1 + successes) / (1 + n_draws)
    se = math.sqrt(max(p_estimate * (1 - p_estimate), 0.0) / (1 + n_draws))
    z = float(stats.norm.ppf(1 - (1 - error_confidence) / 2))
    return PValueResult(
        method="monte-carlo-signflip",
        effect=float(effect),
        statistic_name="abs_signed_sum",
        statistic_observed=observed,
        p_value=p_estimate,
        n_pairs=n,
        n_assignments=n_assignments(n),
        count_as_extreme=1 + successes,
        n_draws=n_draws,
        mc_standard_error=se,
        mc_error_halfwidth=z * se,
        mc_error_confidence=error_confidence,
        seed=seed if seed is not None else None,
        diagnostic={
            "enumeration": f"{n_draws} iid uniform paired sign-flip draws",
            "ties_counted_with_observed": True,
            "observed_assignment_included": True,
        },
    )


# ---------------------------------------------------------------------------
# Exact inversion via breakpoint sweep
# ---------------------------------------------------------------------------

def _contribution_interval(s_value: float, b_value: int,
                           s_observed: float, n: int
                           ) -> tuple[float, float] | None:
    """Return the tau interval on which one assignment is >= observed.

    Roots of ``|S_s - tau B_s| = |S_obs - tau n|``. A generic assignment
    (``|B_s| < n``) contributes on the closed interval between the two roots;
    the difference of squares is a concave quadratic in tau. The two balanced
    extremes (all signs equal) tie everywhere, and the degenerate ``|B_s|=n``
    case otherwise contributes on a half-line. ``None`` means never.
    """
    if b_value == n and math.isclose(s_value, s_observed, abs_tol=1e-10):
        return (-math.inf, math.inf)  # observed assignment: tied everywhere
    if b_value == -n and math.isclose(s_value, -s_observed, abs_tol=1e-10):
        return (-math.inf, math.inf)  # its global flip: tied everywhere

    if abs(b_value) == n:
        # Degenerate linear case (cannot arise from enumerated sign vectors
        # except the two ties above; kept exact for completeness).
        if b_value == n:
            root = (s_value + s_observed) / (2.0 * n)
            return (root, math.inf) if s_value > s_observed \
                else (-math.inf, root)
        root = (s_observed - s_value) / (2.0 * n)
        return (root, math.inf) if (s_value + s_observed) > 0 \
            else (-math.inf, root)

    a = float(b_value) ** 2 - float(n) ** 2  # strictly < 0 here
    b = -2.0 * (s_value * b_value - s_observed * n)
    c = s_value ** 2 - s_observed ** 2
    disc = b * b - 4.0 * a * c
    if disc < -1e-8:
        return None
    root = math.sqrt(max(disc, 0.0))
    r1 = (-b - root) / (2.0 * a)
    r2 = (-b + root) / (2.0 * a)
    return (r2, r1) if r1 > r2 else (r1, r2)


def invert_confidence_set_exact(differences: np.ndarray,
                                alpha: float) -> IntervalSet:
    """Invert the exact test over the constant effect tau.

    Returns the exact acceptance set ``{tau : p(tau) >= alpha}`` as an ordered
    tuple of disjoint intervals, recovered by sweeping the finite set of
    statistic-crossing breakpoints. Gaps are real and are preserved.
    """
    d = np.asarray(differences, dtype=float)
    n = d.size
    total = n_assignments(n)
    s_values, b_values = enumerate_signed_sums(d)
    s_observed = float(d.sum())

    starts: list[float] = []
    ends: list[float] = []
    everywhere = 0
    for s_val, b_val in zip(s_values.tolist(), b_values.tolist()):
        contrib = _contribution_interval(float(s_val), int(b_val),
                                         s_observed, n)
        if contrib is None:
            continue
        r1, r2 = contrib
        if math.isinf(r1) and math.isinf(r2):
            everywhere += 1
        else:
            starts.append(r1)
            ends.append(r2)

    points = np.array(sorted(set(starts + ends)), dtype=float)
    plus_at: dict[float, int] = {
        float(x): int(c) for x, c in zip(*np.unique(np.asarray(starts),
                                                    return_counts=True))
    } if starts else {}
    minus_at: dict[float, int] = {
        float(x): int(c) for x, c in zip(*np.unique(np.asarray(ends),
                                                    return_counts=True))
    } if ends else {}

    def accepted(count: int) -> bool:
        return count / total >= alpha - 1e-15

    # The real line is partitioned into disjoint cells: the open left ray,
    # then for every breakpoint x_j the singleton {x_j} and the open slice to
    # its right. Each cell carries its own assignment count. Consecutive
    # accepted cells are exactly the connected components of the acceptance
    # set; endpoint inclusivity falls out of whether the boundary point
    # itself is accepted.
    cells: list[tuple[Interval, bool]] = []
    open_count = everywhere
    if points.size:
        cells.append((
            Interval(-math.inf, float(points[0]), False, False),
            accepted(open_count),
        ))
    for j, x in enumerate(points.tolist()):
        x = float(x)
        point_count = open_count + plus_at.get(x, 0)
        cells.append((Interval(x, x, True, True), accepted(point_count)))
        open_count = point_count - minus_at.get(x, 0)
        right = float(points[j + 1]) if j + 1 < points.size else math.inf
        cells.append((Interval(x, right, False, False), accepted(open_count)))

    components: list[Interval] = []
    run: Interval | None = None
    for fragment, ok in cells:
        if not ok:
            if run is not None:
                components.append(run)
                run = None
            continue
        if run is None:
            run = fragment
        else:
            run = Interval(
                lower=run.lower,
                upper=fragment.upper,
                lower_inclusive=run.lower_inclusive,
                upper_inclusive=fragment.upper_inclusive,
            )
    if run is not None:
        components.append(run)

    return IntervalSet(
        intervals=tuple(components),
        grid_resolution=None,
        note="exact inversion by breakpoint sweep of the exact two-sided test",
    )


# ---------------------------------------------------------------------------
# Grid profile and Monte-Carlo inversion
# ---------------------------------------------------------------------------

def exact_pvalue_profile(differences: np.ndarray,
                         taus: np.ndarray,
                         chunk_size: int = 2_048) -> np.ndarray:
    """Exact p(tau) on a tau grid using precomputed signed sums.

    The tau grid is processed in chunks; each evaluation is an exact count over
    the full randomization set, never a resample.
    """
    d = np.asarray(differences, dtype=float)
    taus = np.asarray(taus, dtype=float)
    s_values, b_values = enumerate_signed_sums(d)
    n = d.size
    total = n_assignments(n)
    out = np.empty(taus.size, dtype=float)
    s_observed = float(d.sum())
    for start in range(0, taus.size, chunk_size):
        block = taus[start:start + chunk_size]
        observed = np.abs(s_observed - block * n)
        null_stats = np.abs(
            s_values[None, :] - block[:, None] * b_values[None, :]
        )
        counts = np.count_nonzero(
            null_stats >= observed[:, None] - 1e-12, axis=1
        )
        out[start:start + block.size] = counts / total
    return out


def mc_pvalue_profile(differences: np.ndarray, taus: np.ndarray,
                      n_draws: int, seed: int | None = None
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Monte-Carlo p(tau) on a grid from one shared stream of sign draws.

    Returns ``(p_values, successes, se)``. Draws are reused across tau (valid
    random-draw coupling) via precomputed signed sums ``S = signs @ d`` and
    balances ``B = signs.sum()``.
    """
    d = np.asarray(differences, dtype=float)
    taus = np.asarray(taus, dtype=float)
    n = d.size
    rng = np.random.default_rng(seed)
    s_draws = np.empty(n_draws, dtype=np.float64)
    b_draws = np.empty(n_draws, dtype=np.int64)
    pos = 0
    chunk = min(n_draws, 1_000_000)
    while pos < n_draws:
        m = min(chunk, n_draws - pos)
        signs = 2 * rng.integers(0, 2, size=(m, n), dtype=np.int8) - 1
        s_draws[pos:pos + m] = signs.astype(np.float64) @ d
        b_draws[pos:pos + m] = signs.sum(axis=1)
        pos += m

    s_observed = float(d.sum())
    observed = np.abs(s_observed - taus * n)
    null_stats = np.abs(
        s_draws[None, :] - taus[:, None] * b_draws[None, :]
    )
    successes = np.count_nonzero(
        null_stats >= observed[:, None] - 1e-12, axis=1
    )
    p_values = (1 + successes) / (1 + n_draws)
    se = np.sqrt(np.maximum(p_values * (1 - p_values), 0.0) / (1 + n_draws))
    return p_values, successes.astype(float), se


def mc_invert_confidence_set(differences: np.ndarray, alpha: float,
                             n_draws: int, grid: np.ndarray,
                             seed: int | None = None,
                             error_confidence: float = 0.95
                             ) -> tuple[IntervalSet, np.ndarray, float]:
    """Invert Monte-Carlo p-values on a grid.

    Returns the interval set (gaps preserved), the grid p-values, and the
    worst-case MC half-width over the grid. Unlike the exact sweep this is
    genuinely approximate; boundaries carry at least one grid step plus MC
    sampling error.
    """
    from app.core.intervals import intervals_from_mask

    grid = np.asarray(grid, dtype=float)
    p_values, _, se = mc_pvalue_profile(
        differences, grid, n_draws=n_draws, seed=seed
    )
    accepted = p_values >= alpha
    interval_set = intervals_from_mask(grid, accepted)
    z = float(stats.norm.ppf(1 - (1 - error_confidence) / 2))
    max_halfwidth = float(z * se.max(initial=0.0))
    resolution = interval_set.grid_resolution
    note = (
        f"Monte-Carlo inversion: {n_draws} paired sign-flip draws reused at "
        f"every grid point; endpoints carry up to one grid step"
        + (f" ({resolution:.4g})" if resolution else "")
        + f" plus MC error (max half-width {max_halfwidth:.4g} at "
        f"{error_confidence:.0%} confidence)"
    )
    return (
        IntervalSet(
            intervals=interval_set.intervals,
            grid_resolution=resolution,
            note=note,
        ),
        p_values,
        max_halfwidth,
    )
