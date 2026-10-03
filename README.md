# Chebyshev Expansion + Clenshaw Evaluation on a Finite Interval

C++20 / CMake / Eigen implementation with genuinely independent verification.

Given a continuous function `f : [lo, hi] -> R`, the library:

1. maps `[lo, hi]` to the reference interval `[-1, 1]`;
2. samples at the **closed** Chebyshev nodes of the second kind (both physical
   endpoints included);
3. fits Chebyshev coefficients by a discrete cosine transform (DCT-I);
4. evaluates the interpolant (or a degree-`m` truncation) by Clenshaw;
5. keeps **measured sampling/fit error** separate from the **estimated
   truncation tail**, and refuses to claim a strict global error bound without
   an explicit smoothness assertion.

## Quick start (native Linux, no containers, no system installs)

```sh
./scripts/fetch_deps.sh   # downloads pinned CMake 3.28.3 + Eigen 3.4.0 into ./deps
./scripts/build.sh        # project-local CMake build into ./build
./scripts/verify.sh       # deps + build + ctest + independent + demo checks
```

Individual tools:

```sh
./deps/cmake-3.28.3-linux-x86_64/bin/ctest --test-dir build --output-on-failure
./build/bench_ref exp 40                         # independent long-double report
./build/gen_fixture abs 64                       # regenerate numeric fixtures
./build/cheb_demo abs 64 16 -1 1 config/tolerance_relaxed.conf
```

## Layout

| Path | Responsibility |
| --- | --- |
| `src/cheb/interval.hpp` | interval validation, affine mapping, closed nodes |
| `src/cheb/expansion.hpp` | DCT-I Chebyshev fitting, endpoint weights |
| `src/cheb/evaluate.hpp` | Clenshaw evaluation, truncation evaluation |
| `src/cheb/errors.hpp` | measured error vs tail estimate, verdicts/reasons |
| `src/cheb/diagnostics.hpp` | request ids, JSON-lines logging, redaction |
| `src/cheb/config.hpp` | tolerance profiles from `config/*.conf` |
| `src/reference/reference.hpp` | independent `long double` kernel (Kahan sums, explicit basis) |
| `tools/bench_ref.cpp` | independent high-precision coefficient/residual report |
| `tools/gen_fixture.cpp` | regenerate numeric cross-check tables |
| `tools/cheb_demo.cpp` | end-to-end fit -> evidence -> verdict CLI |
| `tests/` | concrete assertions with failure categories |
| `tests/fixtures/` | hand-authored exact fixtures + generated cross-checks |
| `config/` | acceptance profiles |
| `docs/` | numerical contract, error semantics, boundary semantics |

## Dependency pins (SHA-256 checked in `scripts/fetch_deps.sh`)

- CMake `3.28.3` linux-x86_64,
  `804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6`
- Eigen `3.4.0` headers,
  `8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72`

Everything runs as native local processes; no business accounts or network
services beyond dependency downloads.

## What the verdicts mean

- `accepted`: independent residual evidence is within tolerance; any dropped
  tail is either at the roundoff floor (strict finite-interpolant L1 bound)
  or explicitly labelled heuristic.
- `rejected`: measured error exceeds tolerance (`residual_exceeds_tolerance`).
- `indeterminate`: the result cannot be certified — algebraic coefficient
  decay of a non-smooth function, unresolved coefficients, or missing
  independent evidence / smoothness assertion.

See `docs/numerical_contract.md` and `docs/error_semantics.md` for precise
boundary semantics, and `docs/verification.md` for what is and is not checked.
