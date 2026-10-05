# Design Notes

## Data Flow

```
SolverOptions ──> LegendreSolver::compute(n) ──> GaussRule on [-1, 1]
                                                      │ map_to(a, b)
                                                      v
                                              GaussRule on [a, b]
                                                      │
              ┌───────────────────────────────────────┼───────────────────┐
              v                                       v                   v
   analyze_exactness(rule, tol)          golub_welsch_legendre(n)   quadrature sums
   -> ExactnessReport                    -> RawRule (reference)     (user code)
```

Modules communicate only through `Result<T>` values and the value types
`GaussRule` / `ExactnessReport` / `RawRule`. There are no global states;
`LegendreSolver` is stateless and reusable, and a rejected request does not
poison subsequent ones (covered by a test).

## Kernel

Roots of the Legendre polynomial P_n are refined by Newton iteration from
the Tricomi initial guess `x ≈ cos(pi (i + 3/4) / (n + 1/2))`, evaluating
P_n and P'_n by the three-term recurrence. Only the roots in (0, 1] are
iterated; the rest are mirrored, which makes the returned rule symmetric
bit-for-bit. For odd n the middle root is snapped to exactly 0 (its exact
mathematical value) so the mirroring cannot introduce a 1-ulp asymmetry.
Convergence is declared when `|dx| <= tol * max(1, |x|)` with
`tol = 8 * eps` by default; the default budget of 100 iterations is far
more than the observed 3-5.

A root that fails to converge, leaves (-1, 1), or yields a non-positive or
non-finite weight aborts the whole computation with
`ErrorCategory::kComputationFailure`. The `Result` then carries no value,
so an unconverged node can never escape into a returned rule.

## Exactness Interpretation

`analyze_exactness` compares quadrature moments `sum_i w_i x_i^k` against
analytic moments `(b^(k+1) - a^(k+1))/(k+1)` for degrees `0 .. 2n+2` and
reports:

- `theoretical_exact_degree = 2n - 1` (exact arithmetic),
- `verified_exact_degree`: highest degree, consecutive from 0, whose
  residual `|Q_k - I_k| / max(1, |I_k|)` stays within tolerance,
- `first_failure_degree` and `max_rel_residual_within_theory`.

The denominator `max(1, |I_k|)` keeps the measure meaningful for the
vanishing odd moments on symmetric intervals. Degrees above 2n-1 can pass
in floating point because the true error (e.g. for x^2n) underflows the
tolerance; the report states this explicitly instead of overclaiming.

## Independent References

Two references independent of the kernel are used:

1. `tools/gen_reference_fixtures.cpp` evaluates hand-derived closed forms
   (Abramowitz & Stegun 25.4) for n = 1..5 and writes a TSV fixture at
   build time. The tests compare the kernel against this file to 1e-13.
2. `benchmark/golub_welsch.cpp` builds the symmetric tridiagonal Jacobi
   matrix (off-diagonal `k / sqrt(4k^2 - 1)`) and solves it with Eigen's
   `SelfAdjointEigenSolver`; nodes are eigenvalues, weights are
   `2 * v_{1,i}^2`. `benchmark_main` tabulates agreement for n up to 256
   (observed: <= 3.7e-15 in nodes, <= 6.7e-15 in weights).

## Logging

`support/run_log.hpp` gives every process a `run_id`
(`YYYYMMDDTHHMMSSZ-<pid>-<stream>`) and appends JSONL events to
`build/logs/<stream>_<run_id>.jsonl`. Event kinds:

- `test_begin` / `test_end`: bracket a test case, with check/failure counts;
- `check`: one judgment - expression, status, rationale, source location,
  and for numeric checks the actual/expected/diff/tolerance, for error
  checks the expected and actual `ErrorCategory` plus diagnostics;
- `state`: intermediate values (weight sums, residuals, per-order
  discrepancies) that explain later judgments;
- benchmark/example streams log their own domain events (`cross_check`,
  `integration`, `error_contract_demo`).

Because every check records its inputs, tolerances and rationale, a failure
can be replayed and understood from the log file alone.
