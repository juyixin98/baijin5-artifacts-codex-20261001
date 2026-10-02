# Verification record

All commands run natively on Linux x86_64 (g++ 13, project-local CMake 3.30.5
and Eigen 3.4.0 under `.deps/`), no containers.

## 1. Bootstrap

```sh
$ ./scripts/setup_deps.sh
[setup] CMake: cmake version 3.30.5
[setup] Eigen headers: .../.deps/eigen-3.4.0/Eigen/Core
[setup] done. Run: source .deps/env.sh
```

## 2. Clean build + ctest (`./scripts/build.sh`)

```
1/4 Test #1: unit_tests .......................   Passed    0.01 sec
2/4 Test #2: integration_tests ................   Passed    0.01 sec
3/4 Test #3: cli_smoke_rigid ..................   Passed    0.00 sec
4/4 Test #4: cli_smoke_collinear ..............   Passed    0.00 sec

100% tests passed, 0 failed out of 4
```

`unit_tests` (23 cases) — concrete assertions, including:

```
PASS contract/empty-input-diagnosed
PASS contract/negative-and-zero-weight-detected
PASS fit/rigid2d-exact-handmade-reference
PASS fit/similarity3d-exact-handmade-reference
PASS fit/orthogonal2d-refuses-scale-with-known-residual
PASS fit/reflection2d-mode-separation
PASS fit/collinear2d-proper-unique-reflect-nonunique
PASS fit/collinear3d-proper-nonunique-multiplicity
PASS fit/coincident-source-scale-unidentifiable
PASS fit/weights-change-result-closed-form-angle
PASS fit/weighted-noisy2d-matches-independent-angle-scan
PASS fit/failure-categories-are-specific
...
[ok] 23 passed, 0 failed, 23 total
```

## 3. Handmade rotation/translation/scale checks

`rigid2d_exact.csv` (R = 90°, t = (2,-3)) JSON excerpt:

```
"rotation": [[0,-1],[1,0]],
"scale": 1,
"translation": [2,-3],
"det_rotation": 1,
"rmse": 0,
"rotation_unique": true
```

`similarity3d_exact.csv` (120° R, s=2, t=(1,-2,3)): `scale=2`,
`det(R)=1`, `rmse ≈ 9e-16`.

## 4. Determinant / residual separation for a reflection fixture

`reflection2d_exact.csv` is a pure mirror plus translation:

- `--mode rigid-reflect`: `det(R)=-1`, `rmse=0`.
- `--mode rigid`: `det(R)=+1`, best proper turn is 180° and `sse=4, rmse=1`
  (hand-derived for the unit square).

## 5. Collinear multi-solution diagnosis

`collinear2d_reflect.csv`, `--mode rigid-reflect`:

```
uncertainties:
  - [singular-non-unique] rotation-uniqueness: reflection-allowed orthogonal
    map is non-unique for a rank-deficient point cloud
    (rank=1 d=2 singular_values=[10, 0])
identifiability: rotation_unique=false scale_identifiable=true
sse=0 rmse=0
```

The same data in `--mode rigid` is unique (the proper 90° turn) with `rmse=0`.
`collinear3d_proper.csv` in `--mode rigid` is non-unique even for proper
rotations, and the test exhibits two distinct det+1 rotations with zero SSE.

## 6. Scale identifiability

`coincident_source2d.csv` in `--mode similarity` reports
`scale_identifiable=false` plus a `scale-identifiability` warning; the minimum
SSE equals the variance of the target about its own centroid regardless of
scale.

## 7. Failure category on invalid input

A zero-weight CSV returns exit code 1 and renders the specific category:

```
status: FAILED
failures:
  - [total-weight-zero] validate: sum of weights must be strictly positive
```

## 8. Benchmark smoke

```sh
$ ./scripts/bench.sh --iterations 30
rigid_d3_n64      mean≈0.011 ms rmse≈9e-16
rigid_d3_n512     mean≈0.037 ms rmse≈2e-15
rigid_d3_n4096    mean≈0.45  ms rmse≈2e-15
collinear_d3_n1024 (degenerate, proper) mean≈0.09 ms rmse≈8e-16
```

Timings vary with hardware; the benchmark is a relative/perf-smoke tool, and
residuals stay at machine precision for exact synthetic inputs.
