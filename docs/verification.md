# Verification

## What is executed (and currently passes)

`scripts/verify.sh` performs, using only native processes:

1. pinned dependency fetch with SHA-256 verification (`scripts/fetch_deps.sh`);
2. Release build with the project-local CMake (`scripts/build.sh`);
3. the CTest suite (8 executables, working directory = repo root so configs
   and fixtures are read from their committed paths):
   - `test_mapping` — mapping round-trip, endpoints included, bad inputs;
   - `test_expansion` — exact `t^2`, `t^4`, mapped `x^2` coefficients,
     endpoint-weight regression, `sin`/Bessel and `|x|` analytic values;
   - `test_clenshaw` — endpoints, interior, vectorised evaluation, node-exact
     vs off-node cusp error, mapped interval;
   - `test_errors` — geometric/roundoff/algebraic tails, fit-vs-truncation
     separation, `|x|` and `sign` verdict categories, missing-evidence case;
   - `test_diagnostics` — request ids, redaction, structured log fields,
     config profiles;
   - `test_reference_crosscheck` — double core vs independent long-double
     kernel on both cores vs the hand-authored fixture files;
   - `test_generated_fixture` — runs `gen_fixture` as a separate process and
     compares its long-double table to the double core;
   - `test_e2e` — end-to-end verdicts for `exp`, `|x|`, `sign`, `t^4`;
4. independent `bench_ref` reports for `exp` (residual ~3e-18 long double)
   and `|x|` (~9.3e-3, showing the cusp does not silently "pass");
5. three `cheb_demo` verdicts: `exp` accepted, `|x|` rejected under the
   strict profile, `|x|` indeterminate under the relaxed profile.

## Independence of the reference answers

Exact answers are **not** produced by the code under test:

- `tests/fixtures/*.tsv` are hand-authored from Chebyshev identities;
- analytic `sin`/`|x|` coefficients come from Bessel-function and Fourier
  series formulas hard-coded in the tests;
- the numeric cross-check kernel (`src/reference/`) uses `long double`,
  Kahan-compensated direct cosine sums, and an explicit-basis evaluator — a
  different algorithm from the double DCT + Clenshaw core.

## Checks that are NOT performed (do not read as "passing")

- No bound is proven for functions whose smoothness is not asserted; the
  geometric-tail extension remains labelled heuristic.
- The demo's residual evidence is computed in `double`; the `long double`
  evidence exists via `bench_ref` but the two are not auto-fused into a
  single certificate.
- Performance/complexity benchmarks are not included (the DCT is the direct
  O(n^2) form, not an FFT).
- Only Linux x86_64 with g++ 13/14 and 80-bit `long double` is exercised; on
  MSVC-like toolchains the precision guard skips the extended-precision
  tests by design.
