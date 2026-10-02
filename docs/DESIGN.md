# Design notes

## Problem

Given pre-paired point correspondences `(p_i, q_i)` and non-negative weights
`w_i`, find rotation `R`, optional uniform scale `s`, and translation `t`
minimizing

```
E(R, s, t) = sum_i w_i || q_i - (s R p_i + t) ||^2
```

subject to `R^T R = I` and, in proper modes, `det(R) = +1`.

## Weighted Kabsch / Procrustes pipeline

1. **Validate** the numerical contract (`procrustes_contract`).
2. **Weighted centroids**
   `pbar = sum w p / W`, `qbar = sum w q / W`, `W = sum w`.
3. **Centred, weighted matrices**
   `X = [sqrt(w_i)(p_i - pbar)]`, `Y = [sqrt(w_i)(q_i - qbar)]`.
   Weighted variances are `var_p = ||X||²F / W`, `var_q = ||Y||²F / W`.
4. **Cross-covariance** `H = X Y^T`, SVD `H = U Σ V^T`.
5. **Orthogonal factor** with sign matrix `D = I`; for proper modes flip the
   last entry when `det(V U^T) < 0`, then `R = V D U^T`.
6. **Scale** (similarity only): `s = tr(D Σ) / ||X||²F`; fixed at 1 for
   orthogonal modes. When `var_p = 0` the scale is unidentifiable and `s` is
   reported as 0 with an explicit warning.
7. **Translation**: `t = qbar - s R pbar`.
8. **Quality**: per-point residual norms, weighted SSE, RMSE
   `sqrt(SSE/W)`, max residual.

## Uniqueness analysis

Rank of `H` is estimated against `rank_tol * max(1, σ_max)`.

- `rank = 0`: rotation undetermined.
- Reflection allowed: uniqueness requires `rank = d`; any orthogonal action in
  the null space leaves the residual unchanged.
- Proper only: in 2D `rank = 1` still fixes the unique rotation between the two
  oriented lines (sign chosen to satisfy det +1); generally uniqueness
  requires `rank >= d - 1`.

Each case sets `rotation_unique` and emits a `singular-non-unique` warning that
includes rank, dimension and singular values.

## Traceability

`FitResult` always carries `request_id`, `version` (see
`version.hpp`), ordered stage `Diagnostic` records (centroids → SVD → rotation
→ solution → uniqueness), and separated `failures` / `uncertainties` / `steps`
in both text and JSON renderers. The logger prepends severity, request id,
version, component and `file:line:function` to every line.

## Module boundaries

- `contract` has no solver code; the kernel depends on it.
- `core` performs the fit only; presentation lives in `explain`.
- `bench` generates workloads independently of the core (its own QR-based
  random rotation and seeded PRNG), so benchmark inputs are not produced by the
  code being measured.
- Tests derive expected answers independently: hand-built matrices, closed
  forms (2D angle from cross-covariance components), and a fine 2D angle scan.
