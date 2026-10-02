# Padé Rational Approximation — C++20 / CMake / Eigen

A reviewable, layered implementation of the **[m/n] Padé approximant** of a
power series, with explicit **degeneracy diagnosis**, term-by-term **residual
verification**, and **common-factor elimination that never erases the original
local [m/n] definition**.

All arithmetic is `long double`; the Toeplitz solve uses Eigen 3.4
`JacobiSVD`. Everything runs natively on Linux x86_64 — no containers, no
system installs, no external services or accounts.

## What it computes

For `C(x) = Σ c_k x^k` and orders `m, n`, the Padé approximant is

```
R(x) = P(x) / Q(x),  deg P = m, deg Q = n, Q(0) = 1,
C(x) Q(x) − P(x) = O(x^(m+n+1)).
```

With `Q(x) = 1 + q_1 x + … + q_n x^n`, the denominator solves the n×n
**Toeplitz** system

```
Σ_{j=1..n} c_{m+i−j} q_j = −c_{m+1+i},   i = 0..n−1          (c_k = 0 if k<0),
```

and then `p_k = c_k + Σ_{j=1..k} q_j c_{k−j}` for `k = 0..m`.

### Matching order is verified term by term

The kernel never *assumes* the order. It forms the direct convolution residual

```
D_k = (C·Q)_k − p_k,   k = 0,1,2,…
```

and `matched_terms` is the count of consecutive coefficients that vanish
within tolerance. A normalized solution is only accepted when the first
`m+n+1` residual coefficients are zero; the first nonzero index is reported as
the actual match boundary (e.g. exp `[2/2]` gives `D_5 ≠ 0`, matching order 4).

## Degeneracy semantics (the central contract)

Denominator normalization `Q(0) = 1` is **not always possible**. The singular
values of the Toeplitz block decide the branch (rank `r` vs `n`):

| Situation | `StatusCode` | `q0_normalized` | Meaning / action |
|-----------|--------------|-----------------|------------------|
| Full rank, unique solve | `Ok` | true | Normal [m/n] approximant. |
| Singular **but consistent** system | `RankDeficient` | true | Minimum-norm `q0=1` representative returned; non-fatal category, exit 0. |
| Singular **and inconsistent** system | `DegenerateDenominatorConstant` | **false** | `q0=1` is infeasible (block Padé / Frobenius block). A `q0=0` homogeneous representative is kept for diagnosis; this is a **failure**, exit 3. |
| Normalized solve but residual doesn't vanish | `ResidualMismatch` | true | Numerical inconsistency; treated as failure. |
| `|Q(x)|` at/below relative tolerance on evaluation | `DenominatorNearZero` | — | Evaluation refuses and reports the denominator; exit 4. Never returns ±inf as a value. |
| Bad orders / too few coefficients | `InvalidArgument` | — | exit 2. |
| Non-finite SVD output | `NumericalFailure` | — | exit 3. |

`isFailure()` returns true for the fatal rows only; `RankDeficient` and `Ok`
are usable. Unknown/exceptional states are **never** folded into “success”.

**Worked degeneracy example.** `f(x) = 1 + x^2` at box `[1/1]`: the single
equation at order 2 is `c_2 + c_1 q_1 = 1 + 0·q_1 = 0`, i.e. `1 = 0`,
inconsistent. The engine reports `DegenerateDenominatorConstant`, rank 0,
`q0_normalized=false`, and retains the homogeneous representative
`q=(0,1)` plus unreduced `p=(1,1)`.

### Common-factor elimination preserves local information

When `P,Q` share a factor (common for rank-deficient boxes), the kernel:

- leaves `report.p` / `report.q` as the **unreduced local [m/n] definition**
  (always, untouched),
- computes a monic Euclidean GCD and stores its degree/coefficients
  (`gcd_degree`, `gcd_coeffs`),
- stores the divided fraction **separately** in `p_reduced` / `q_reduced`,
  together with the division remainder norm (`reduced_residual_norm`).

Example: the geometric series `1/(1−x)` at box `[2/2]` yields the
minimum-norm unreduced `P=(1,½,0)`, `Q=(1,−½,−½)` with GCD degree 1
(proportional to `1+½x`); the reduced arrays represent `1/(1−x)`. The residual
is reported against the **unreduced** local definition.

## Project layout

```
include/pade/        public numerical contract
  types.hpp           StatusCode, SolveOptions, SolveReport, EvalReport
  solver.hpp          solvePade(), evaluate()
  polynomial.hpp      Euclidean GCD, polynomial division/trim/eval
  config.hpp          key=value configuration layer
  seriesio.hpp        synthetic series generators + coefficient file IO
  service.hpp         JSON rendering + HTTP request parsing
  logging.hpp         run-id correlated structured logs
  version.hpp
src/pade/            algorithm kernel + error explanation (one file per header)
src/service/         runnable CLI and loopback HTTP entry (service layer)
tests/               independent test layer (30 assertion-based cases)
benchmarks/          independent libm-referenced convergence benchmark
config/              default numerical-contract configuration
scripts/             fetch_deps / build / test / demo (POSIX shell)
data/                synthetic coefficient fixtures
third_party/         project-local CMake + Eigen (fetched, checksum-pinned)
```

Layering matches the requested organization: **numerical contract**
(`types.hpp`) → **algorithm kernel** (`solver.cpp`, `polynomial.cpp`) →
**error explanation** (status codes + `reason` + logs) → **independent
benchmark** (`benchmarks/`, referencing libm only) → separate **tests** and
**config** layers.

## Quick start (native, no root/container)

```bash
# 1. fetch project-local CMake 3.30.5 and Eigen 3.4.0 (pinned SHA-256)
scripts/fetch_deps.sh

# 2. configure + build with the project-local CMake
scripts/build.sh

# 3. run the full suite (ctest + verbose binary)
scripts/test.sh

# 4. end-to-end local demo (CLI, degenerate case, pole, benchmark, HTTP)
scripts/demo.sh
```

Artifacts: `build/src/service/pade_cli`, `build/tests/test_pade`,
`build/pade_benchmark`.

### CLI

```bash
CLI=build/src/service/pade_cli
$CLI version
$CLI gen-series --kind exp --terms 12 --out data/exp_12.txt
$CLI approx --m 2 --n 2 --series data/exp_12.txt --eval 0.25
$CLI approx --m 1 --n 1 --series data/poly_1px2.txt        # exit 3 (degenerate)
$CLI approx --m 1 --n 1 --kind exp --terms 8 --eval 2     # exit 4 (pole)
$CLI serve --port 18083
```

Each report is JSON with a unique `run_id`, explicit `status`/`failure`,
singular values, rank, condition number, unreduced and reduced polynomials,
and the residual vector. Human-readable progress logs (including the rank
decision and the first nonzero residual) go to stderr, all keyed by the same
`run_id`.

### HTTP service (loopback only)

```bash
$CLI serve --port 18083 &
curl -s http://127.0.0.1:18083/health
curl -s -X POST http://127.0.0.1:18083/approx \
  -H 'Content-Type: application/json' \
  -d '{"m":2,"n":2,"coefficients":[1,1,0.5,0.166666666666666666,0.041666666666666666,0.008333333333333333]}'
```

`200` for `Ok`/`RankDeficient`; `422` for degenerate/numerical failures;
`400` for malformed JSON.

## Test design (assertions are concrete, oracles are independent)

The suite (in `tests/`) asserts specific numbers and failure categories, not
“the interface can be called”:

- `test_exact_exp.cpp` — exp `[1/1]`, `[2/2]`, `[3/2]`, `[0/0]` against
  hand-computed exact rationals (e.g. `(1+x/2+x²/12)/(1−x/2+x²/12)`).
- `test_rank_degeneracy.cpp` — the infeasible `q0=1` case (`1+x²` at
  `[1/1]` → `DegenerateDenominatorConstant`), the consistent singular case
  (constant series → `RankDeficient`), and geometric `[2/2]` minimum-norm +
  GCD, plus `InvalidArgument` cases.
- `test_evaluation.cpp` — exp values vs libm, exact pole `x=2` and near-pole
  detection (`DenominatorNearZero`), evaluation through the reduced fraction.
- `test_common_factor.cpp` — Euclidean GCD on hand-factored polynomials,
  coprime case, interior-zero trim correctness, and confirmation that
  unreduced arrays survive elimination.
- `test_oracles.cpp` — expected answers produced **without** the engine:
  explicit Cramer's rule for n=1 and n=2 denominators, independently
  recomputed convolution residual, libm `std::exp`, and an independent Taylor
  summation.
- `test_config_layer.cpp` — config parse/error/unknown-key semantics,
  synthetic generators, and coefficient-file round-trip.

Run logs show engine version, per-case progress, singular values, rank,
residual boundary and the `run_id`; a failure prints the concrete mismatch.

### Benchmark

`build/pade_benchmark --max-k 6 --points 9 --xmax 0.5` scans exp `[k/k]`,
re-verifies each match order, reports Toeplitz condition number and the
maximum relative error against **libm** (independent of the kernel). Observed:
error drops from `6.5e-1` (`[0/0]`) to `~2e-17` (`[6/6]`) at |x|≤0.5.

## Configuration

`config/pade.default.conf` documents and sets tolerances:
`rank_tol_factor`, `residual_tol`, `gcd_tol`, `residual_extra_terms`,
`remove_common_factor`, `pole_relative_tol`, `series_extra_terms`. Pass with
`--config path/to/file`. Parse errors and unknown keys are surfaced, never
silently ignored.

## Exit codes

`0` ok (incl. usable rank-deficient) · `2` invalid argument · `3` degenerate
or numerical failure · `4` denominator near zero · `5` other/unknown.

## Reproducing the key checks manually

```bash
scripts/fetch_deps.sh && scripts/build.sh
scripts/test.sh                                   # expect 30 passed, 0 failed
build/src/service/pade_cli approx --m 1 --n 1 \
  --series data/poly_1px2.txt ; echo $?           # Degenerate..., 3
build/pade_benchmark --max-k 6 --xmax 0.5         # convergence vs libm
```

## Not implemented / scope

- Diagonal Padé and rectangular [m/n] are supported; multipoint or
  multivariate Padé are not.
- The HTTP server is a minimal single-connection, loopback-only utility, not a
  hardened/production server.
- GCD uses a tolerance-based Euclidean algorithm on `long double` (documented),
  not exact symbolic rational arithmetic.
