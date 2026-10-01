"""Estimation kernel: randomization p-values and constant-effect inversion.

The kernel works on within-pair differences ``d_i = treated_i - control_i``.
Under the sharp constant-effect null ``H_tau`` ("every pair's treatment
effect is exactly tau"), the adjusted difference is ``d_i - tau`` and the
randomization statistic is

    S_z(tau) = sum_i z_i (d_i - tau),   z_i in {-1, +1}.

The observed orientation is ``z = +1`` everywhere, so ``S_obs(tau) =
sum_i d_i - n*tau``.

Exact enumeration is vectorized over the full ``2**n`` paired assignment
matrix (a deliberately different path from the test oracle's Fraction /
bit-mask loop).  When ``2**n`` exceeds the configured combination budget the
kernel transparently switches to Monte Carlo and *reports the method and its
error* rather than pretending the result is exact.

Inversion reuses the same two-sided definition used for the p-value.  The
exact certified path reconstructs acceptance components from the analytic
breakpoints; disconnected acceptance sets stay disconnected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import norm

from ..config import Settings
from .contracts import (
    AcceptanceSetResult,
    ComputationKind,
    PValueResult,
    SetComponent,
    TwoSidedMethod,
)

# Statistic values that differ only by a few ULPs are treated as the same
# null atom.  Kept deliberately tiny (not 1e-10): sums of the same addends
# under the same dot-product order are bit-identical, so only last-ULP
# rounding of algebraically-equal-but-differently-grouped sums needs merging,
# and a looser tolerance would absorb genuinely distinct, closely-spaced atoms.
_TIE_TOL = 8.0 * np.finfo(float).eps
_Z95 = float(norm.ppf(0.975))  # normal quantile for the reported 95% MC margin


class ApproximationUnavailable(RuntimeError):
    """Raised when no honest approximation exists for the requested method.

    The probability-ordering two-sided p-value ranks statistic values by
    their *null atom probabilities*. Those multiplicities require the full
    randomization set; a naive Monte Carlo sample estimates them with
    frequency noise that systematically mis-orders equal-mass atoms, so the
    result would be biased. Callers should use exact enumeration (smaller
    design) or the absolute-statistic definition ``two_sided_abs``.
    """


@dataclass(frozen=True)
class _Forms:
    """All assignment statistics as affine forms S_a(tau) = B_a - tau*k_a."""

    b: np.ndarray  # shape (2**n,): sum_i z_ai d_i
    k: np.ndarray  # shape (2**n,): sum_i z_ai
    d_total: float
    n: int

    @property
    def size(self) -> int:
        return self.b.shape[0]


def _build_forms(d: np.ndarray) -> _Forms:
    n = d.shape[0]
    size = 2**n
    bits = (np.arange(size, dtype=np.int64)[:, None] >> np.arange(n, dtype=np.int64)) & 1
    z = 1.0 - 2.0 * bits.astype(float)
    b = z @ d
    k = z.sum(axis=1)
    return _Forms(b=b, k=k, d_total=float(d.sum()), n=n)


def _cluster_masses(values: np.ndarray) -> np.ndarray:
    """Mass (multiplicity) of the null atom each value belongs to.

    Atoms are groups of numerically equal values.  Clustering uses a
    *leader* rule (a value joins the current atom only while within
    tolerance of that atom's first member), which bounds an atom's diameter
    by the tolerance; a single-linkage rule would chain a sequence of
    genuinely distinct, progressively-spaced values into one phantom atom.
    """
    order = np.argsort(values)
    sv = values[order]
    scale = max(1.0, float(np.max(np.abs(sv))))
    atom_id = np.empty(len(sv), dtype=np.int64)
    leaders: List[int] = []
    current = 0
    atom_id[0] = 0
    leaders.append(0)
    for i in range(1, len(sv)):
        if sv[i] - sv[leaders[current]] > _TIE_TOL * scale:
            current += 1
            leaders.append(i)
        atom_id[i] = current
    sizes = np.bincount(atom_id)
    masses_sorted = sizes[atom_id]
    out = np.empty_like(masses_sorted)
    out[order] = masses_sorted
    return out


def _is_tie(x: np.ndarray, y: float, scale: float) -> np.ndarray:
    """Symmetric numerical-equality test (no one-sided threshold shift)."""
    return np.abs(x - y) <= _TIE_TOL * scale


def _extreme_count_abs(stat: np.ndarray, obs_index: int) -> int:
    m0 = abs(stat[obs_index])
    scale = max(1.0, float(m0), float(np.max(np.abs(stat))))
    mag = np.abs(stat)
    # strictly larger in magnitude, or numerically tied with the observed
    # magnitude (symmetric tie test; no one-sided downward threshold shift)
    return int(np.count_nonzero((mag > m0) | _is_tie(mag, m0, scale)))


def _extreme_count_prob(stat: np.ndarray, obs_index: int) -> int:
    masses = _cluster_masses(stat)
    # The observed statistic's own null atom, located by assignment index
    # rather than by near-equality (a near-by search could hit a neighbour).
    obs_mass = int(masses[obs_index])
    return int(np.sum(masses <= obs_mass))


class RandomizationKernel:
    """Kernel for one fixed paired dataset and two-sided definition."""

    def __init__(self, differences: Sequence[float], method: TwoSidedMethod, settings: Settings):
        d = np.asarray(differences, dtype=float)
        if d.ndim != 1 or not np.all(np.isfinite(d)):
            raise ValueError("differences must be a 1-D array of finite numbers")
        self._d = d
        self._method = TwoSidedMethod(method)
        self._settings = settings
        self._forms = _build_forms(d)
        if self._method is TwoSidedMethod.ABS:
            self._extreme = _extreme_count_abs
        elif self._method is TwoSidedMethod.PROB:
            self._extreme = _extreme_count_prob
        else:  # pragma: no cover - guarded by enum
            raise ValueError(f"unsupported two-sided method {method}")

    @property
    def n_pairs(self) -> int:
        return self._forms.n

    @property
    def randomization_set_size(self) -> int:
        return self._forms.size

    @property
    def method(self) -> TwoSidedMethod:
        return self._method

    @property
    def exact_enumeration_required(self) -> bool:
        """True when Monte Carlo would bias this definition (PROB ordering)."""
        return self._method is TwoSidedMethod.PROB

    def _ensure_exact_feasible(self) -> None:
        if (
            self._method is TwoSidedMethod.PROB
            and self._forms.size > self._settings.exact_budget
        ):
            raise ApproximationUnavailable(
                f"probability-ordering two-sided p-values need the full randomization set "
                f"({self._forms.size} assignments) to rank statistic atoms by their null "
                f"probability, which exceeds the exact budget {self._settings.exact_budget}; "
                "reduce n_pairs or use method 'two_sided_abs'"
            )

    # ------------------------------------------------------------------
    # p-values
    # ------------------------------------------------------------------
    def _extreme_count(self, tau: float) -> int:
        """Integer count of assignments as/more extreme (exact enumeration)."""
        forms = self._forms
        stat = forms.b - tau * forms.k
        return self._extreme(stat, 0)

    def p_value(self, tau: float, alpha: Optional[float] = None) -> PValueResult:
        forms = self._forms
        obs = forms.d_total - forms.n * tau
        uncertainty: List[str] = []
        if forms.size <= self._settings.exact_budget:
            stat = forms.b - tau * forms.k
            extreme = self._extreme(stat, 0)  # row 0 is the all-+1 observed assignment
            p = extreme / forms.size
            result = PValueResult(
                tau=float(tau),
                method=self._method,
                kind=ComputationKind.EXACT,
                p_value=p,
                n_pairs=forms.n,
                randomization_set_size=forms.size,
                n_extreme=extreme,
                n_evaluated=forms.size,
                rejected=None if alpha is None else p <= alpha,
                alpha=alpha,
            )
            return result

        self._ensure_exact_feasible()  # raises for PROB over budget
        result = self._monte_carlo(tau, obs, alpha, uncertainty)
        return result

    def _monte_carlo(
        self, tau: float, obs: float, alpha: Optional[float], uncertainty: List[str]
    ) -> PValueResult:
        s = self._settings
        seed = _stable_seed(s.mc_seed, float(tau), self._method.value)
        rng = np.random.default_rng(seed)
        bits = rng.integers(0, 2, size=(s.mc_draws, self._forms.n), dtype=np.int8)
        z = 1.0 - 2.0 * bits.astype(float)
        stat = z @ (self._d - tau)
        # Include the observed assignment in the pooled sample (valid
        # randomization-p value convention (r+1)/(M+1)); it is the last row.
        pooled = np.concatenate((stat, [obs]))
        extreme = self._extreme(pooled, pooled.shape[0] - 1)
        m_total = s.mc_draws + 1
        p = extreme / m_total
        se = _pooled_se(p, m_total)
        uncertainty.append(
            f"exact enumeration of {self._forms.size} assignments exceeds budget "
            f"{s.exact_budget}; p-value estimated from {s.mc_draws} Monte Carlo flips"
        )
        return PValueResult(
            tau=float(tau),
            method=self._method,
            kind=ComputationKind.APPROXIMATE,
            p_value=p,
            n_pairs=self._forms.n,
            randomization_set_size=self._forms.size,
            n_extreme=None,
            n_evaluated=m_total,
            standard_error=se,
            monte_carlo_error=_Z95 * se,
            seed=s.mc_seed,
            rejected=None if alpha is None else p <= alpha,
            alpha=alpha,
            uncertainty=tuple(uncertainty),
        )

    # ------------------------------------------------------------------
    # inversion
    # ------------------------------------------------------------------
    def invert(self, alpha: float) -> AcceptanceSetResult:
        """Return the constant-effect acceptance set {tau : p(tau) > alpha}.

        Strict ``>`` matches the non-rejection region of the test (reject at
        ``p <= alpha``).  Components are returned separately.
        """
        forms = self._forms
        crossing_pairs = forms.size * (forms.size - 1) // 2
        exact_prob_feasible = (
            self._method is TwoSidedMethod.ABS or crossing_pairs <= self._settings.crossing_budget
        )
        if forms.size <= self._settings.exact_budget and exact_prob_feasible:
            return self._invert_exact(alpha)
        # PROB needs full enumeration even for its grid p-values; refuse
        # honestly rather than bias the atom-probability ordering.
        self._ensure_exact_feasible()
        return self._invert_approximate(alpha)

    # -- exact certified inversion -------------------------------------
    def _breakpoints(self) -> np.ndarray:
        forms = self._forms
        if self._method is TwoSidedMethod.ABS:
            # |B_a - tau k_a| crosses |D - tau n| at (B_a - D)/(k_a - n).
            denom = forms.k - forms.n
            mask = denom != 0
            roots = (forms.b[mask] - forms.d_total) / denom[mask]
        else:
            # Probability ordering changes at every pair of crossing forms.
            bi = forms.b[:, None]
            ki = forms.k[:, None]
            iu, ju = np.triu_indices(forms.size, k=1)
            denom = ki.squeeze(1)[iu] - forms.k[ju]
            keep = denom != 0
            roots = (bi.squeeze(1)[iu][keep] - forms.b[ju][keep]) / denom[keep]
        roots = roots[np.isfinite(roots)]
        return _unique_tolerance(roots)

    def _invert_exact(self, alpha: float) -> AcceptanceSetResult:
        roots = self._breakpoints()
        # Ordered alternating cells: gap, point, gap, ... (like the oracle).
        nroots = roots.shape[0]
        mids = np.empty(nroots + 1)
        if nroots == 0:
            mids[0] = 0.0
        else:
            mids[0] = roots[0] - 1.0
            mids[1:nroots] = (roots[:-1] + roots[1:]) / 2.0
            mids[nroots] = roots[-1] + 1.0

        # Acceptance is the EXACT integer test extreme/size > alpha.  Parse
        # alpha as a rational (JSON numbers arrive as their shortest decimal
        # repr) so the boundary comparison has no one-sided floating epsilon
        # that could delete an accepted cell or flip an endpoint.
        alpha_frac = Fraction(str(alpha))
        size = self._forms.size

        def accepted(tau: float) -> bool:
            count = self._extreme_count(float(tau))
            return count * alpha_frac.denominator > alpha_frac.numerator * size

        gap_ok = [accepted(t) for t in mids]
        point_ok = [accepted(r) for r in roots] if nroots else []

        components: List[SetComponent] = []
        in_run = False
        run_lo: Optional[float] = None
        run_start_closed = False
        run_hi: Optional[float] = None
        run_end_closed = False
        # cell sequence index: gaps 0..nroots, points 0..nroots-1 interleaved
        for cell in range(2 * nroots + 1):
            if cell % 2 == 0:  # gap g = cell//2
                g = cell // 2
                ok = bool(gap_ok[g])
                lo = None if g == 0 else float(roots[g - 1])
                hi = None if g == nroots else float(roots[g])
                closed_lo = False
                closed_hi = False
            else:  # point p = cell//2
                g = cell // 2
                ok = bool(point_ok[g])
                lo = hi = float(roots[g])
                closed_lo = closed_hi = True
            if ok:
                if not in_run:
                    run_lo, run_start_closed, in_run = lo, closed_lo, True
                run_hi, run_end_closed = hi, closed_hi
            elif in_run:
                components.append(SetComponent(run_lo, run_start_closed, run_hi, run_end_closed))
                in_run = False
        if in_run:
            components.append(SetComponent(run_lo, run_start_closed, run_hi, run_end_closed))

        return AcceptanceSetResult(
            method=self._method,
            alpha=alpha,
            kind=ComputationKind.EXACT,
            components=tuple(components),
            n_pairs=self.n_pairs,
            randomization_set_size=self.randomization_set_size,
            certified=True,
            hull=_hull(components),
        )

    # -- approximate inversion -----------------------------------------
    def _fixed_p_evaluator(self) -> Tuple["_PEvaluator", ComputationKind, int, float]:
        """Build one p(tau) evaluator reused across the whole inversion.

        Common random numbers: when exact enumeration is infeasible a single
        Monte Carlo flip matrix is drawn once and reused for every tau, so
        the estimated p(tau) is one fixed step function and bisection
        converges instead of chasing pointwise-independent noise.
        """
        s = self._settings
        if self._forms.size <= s.exact_budget:
            forms = self._forms

            def evaluate(tau: float) -> Tuple[float, float]:
                stat = forms.b - tau * forms.k
                return self._extreme(stat, 0) / forms.size, 0.0

            return evaluate, ComputationKind.EXACT, forms.size, 0.0

        rng = np.random.default_rng(
            _stable_seed(s.mc_seed, float(self._forms.d_total), "invert:" + self._method.value)
        )
        z = 1.0 - 2.0 * rng.integers(0, 2, size=(s.mc_draws, self._forms.n)).astype(float)
        zd = z @ self._d  # fixed numerator part; S_a(tau) = zd_a - tau*zk_a
        zk = z.sum(axis=1)
        m_total = s.mc_draws + 1  # +1 for the pooled observed assignment

        def evaluate(tau: float) -> Tuple[float, float]:
            stat = zd - tau * zk
            obs = self._forms.d_total - self._forms.n * tau
            pooled = np.concatenate((stat, [obs]))  # observed assignment last
            p = self._extreme(pooled, pooled.shape[0] - 1) / m_total
            return p, _pooled_se(p, m_total)

        return evaluate, ComputationKind.APPROXIMATE, m_total, 0.0

    def _invert_approximate(self, alpha: float) -> AcceptanceSetResult:
        s = self._settings
        scale = max(1.0, float(np.sum(np.abs(self._d))))
        lo_edge, hi_edge = -scale, scale

        evaluate, p_kind, n_eval, _ = self._fixed_p_evaluator()
        grid = np.linspace(lo_edge, hi_edge, s.inversion_grid)
        pvals = np.array([evaluate(float(t))[0] for t in grid])
        ok = pvals > alpha
        tol = (hi_edge - lo_edge) * 2.0 ** (-s.inversion_refine)
        spacing = (hi_edge - lo_edge) / s.inversion_grid

        components: List[SetComponent] = []
        tau_errors: List[float] = []
        p_ses: List[float] = []
        i = 0
        while i < len(grid):
            if not ok[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(grid) and ok[j + 1]:
                j += 1
            lower, lo_err, lo_se = self._refine_boundary(
                evaluate, grid[i - 1] if i > 0 else lo_edge, grid[i], alpha, tol, spacing,
                inside_right=True,
            )
            upper, hi_err, hi_se = self._refine_boundary(
                evaluate, grid[j], grid[j + 1] if j + 1 < len(grid) else hi_edge, alpha, tol, spacing,
                inside_right=False,
            )
            tau_errors.extend([lo_err, hi_err])
            p_ses.extend([lo_se, hi_se])
            lc = evaluate(lower)[0] > alpha
            uc = evaluate(upper)[0] > alpha
            components.append(SetComponent(lower, bool(lc), upper, bool(uc)))
            i = j + 1

        # Unbounded tails: a component runs to infinity iff a far-out probe
        # is accepted (ordering is frozen beyond every finite breakpoint).
        far = scale * 1e6 + 1.0
        if evaluate(-far)[0] > alpha and components and components[0].lower is not None:
            first = components[0]
            components[0] = SetComponent(None, False, first.upper, first.upper_closed)
            tau_errors[0] = 0.0  # unbounded side carries no finite boundary
        if evaluate(far)[0] > alpha and components and components[-1].upper is not None:
            last = components[-1]
            components[-1] = SetComponent(last.lower, last.lower_closed, None, False)
            tau_errors[-1] = 0.0

        boundary_tau_error = max(tau_errors, default=tol)
        max_p_se = max(p_ses, default=0.0)
        uncertainty = [
            f"inversion evaluated {s.inversion_grid} grid points (spacing {spacing:.3g}) and "
            "bisected transitions on the same fixed evaluation path; accepted components "
            "narrower than the grid spacing (in particular isolated accepted/rejected points) "
            "could be missed, so the union is not certified exhaustive"
        ]
        if p_kind is ComputationKind.APPROXIMATE:
            uncertainty.append(
                f"all p-values are Monte Carlo estimates from one shared sample of "
                f"{s.mc_draws} flips (max binomial SE {max_p_se:.3g}); each finite boundary is "
                f"located to about +/-{boundary_tau_error:.3g} on the effect scale, propagating "
                "MC noise through the local slope of the fixed p(tau) path"
            )
        else:
            uncertainty.append(
                "all grid/boundary p-values are exact enumerations; only the spatial inversion "
                f"is approximate ({s.inversion_grid} grid points), so this uncertainty is about "
                "component completeness, not statistical sampling"
            )
        return AcceptanceSetResult(
            method=self._method,
            alpha=alpha,
            kind=ComputationKind.APPROXIMATE,
            components=tuple(components),
            n_pairs=self.n_pairs,
            randomization_set_size=self.randomization_set_size,
            certified=False,
            boundary_tolerance=boundary_tau_error,
            standard_error=max_p_se if max_p_se > 0 else None,
            n_p_evaluations=s.inversion_grid,
            hull=_hull(components),
            uncertainty=tuple(uncertainty),
        )

    def _refine_boundary(
        self, evaluate, outside: float, inside: float, alpha: float,
        tol: float, spacing: float, *, inside_right: bool,
    ) -> Tuple[float, float, float]:
        """Bisect an accepted/rejected transition on a fixed p(tau) path.

        Returns ``(boundary, tau_error, p_se)`` where ``tau_error`` is a 95%
        error margin on the effect scale: quadrature of the numerical
        resolution and the Monte Carlo noise propagated through the local
        slope of the *same* fixed sample.
        """
        lo, hi = float(outside), float(inside)
        for _ in range(self._settings.inversion_refine):
            if abs(hi - lo) <= tol:
                break
            mid = (lo + hi) / 2.0
            accepted = evaluate(mid)[0] > alpha
            if inside_right:
                hi, lo = (mid, lo) if accepted else (lo, mid)
            else:
                lo, hi = (mid, hi) if accepted else (lo, mid)
        boundary = (lo + hi) / 2.0
        _, p_se = evaluate(boundary)
        tau_error = max(tol, spacing / 2.0)
        if p_se > 0:
            h = max(abs(boundary) * 1e-4, 10.0 * tol, spacing / 100.0, 1e-6)
            p_plus, _ = evaluate(boundary + h)
            p_minus, _ = evaluate(boundary - h)
            slope = abs(p_plus - p_minus) / (2.0 * h)
            if slope > 1e-12:
                tau_error = max(tau_error, _Z95 * p_se / slope)
        return boundary, tau_error, p_se


def _pooled_se(p: float, m_total: int) -> float:
    """SE of the pooled (r+1)/(M+1) Monte Carlo p-value.

    With ``M = m_total-1`` random draws plus the observed assignment, the
    estimator's variance is ``(M/(M+1)) * p(1-p)/(M+1)`` -- slightly smaller
    than the naive ``p(1-p)/(M+1)`` binomial variance.
    """
    m_draws = m_total - 1
    factor = m_draws / m_total if m_total > 0 else 0.0
    return math.sqrt(max(p * (1.0 - p), 0.0) * factor / m_total)


def _stable_seed(base_seed: int, tau: float, method: str) -> int:
    """Deterministic per-(tau, method) seed, independent of PYTHONHASHSEED."""
    tag = f"{method!r}|{tau!r}".encode("utf-8")
    digest = 0
    for byte in tag:
        digest = (digest * 131 + byte) & 0xFFFFFFFFFFFFFFFF
    return base_seed ^ digest


def _unique_tolerance(values: np.ndarray) -> np.ndarray:
    """Sort and collapse breakpoints that coincide up to relative tolerance."""
    v = np.sort(values)
    if v.size == 0:
        return v
    scale = max(1.0, float(np.max(np.abs(v))))
    keep = np.concatenate(([True], np.diff(v) > _TIE_TOL * scale))
    return v[keep]


def _hull(components: Sequence[SetComponent]) -> Optional[SetComponent]:
    """Convex hull, always labelled as a hull (never a substitute union).

    If any component is unbounded on a side, the hull is unbounded there too.
    """
    if not components:
        return None
    low = None if any(c.lower is None for c in components) else min(c.lower for c in components)
    high = None if any(c.upper is None for c in components) else max(c.upper for c in components)
    lc = any(c.lower == low and c.lower_closed for c in components) if low is not None else False
    hc = any(c.upper == high and c.upper_closed for c in components) if high is not None else False
    return SetComponent(low, lc, high, hc)
