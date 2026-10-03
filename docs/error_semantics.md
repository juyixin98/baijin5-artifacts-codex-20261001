# Error semantics: fit error vs truncation tail

The two error channels are deliberately never merged into one number.

## 1. Sampling/fit error — measured

`assess` takes residual vectors at points **independent of the fit nodes**
(a shifted dense grid in tests and the demo):

- `fit_residual`  = max |p_n(x) - f(x)|, full-degree interpolant;
- `truncation_residual` = max |p_m(x) - f(x)|, degree-`m` truncation.

For higher confidence the independent `long double` kernel in
`src/reference/` computes the same quantity with extra precision
(`tools/bench_ref`, `# node_independent_max_residual`). These are
**observations**, not proofs over every point.

Example (double, `exp`):

| degree | truncation | fit residual | truncation residual |
| --- | --- | --- | --- |
| 6 | 2 | ~6e-6 (roundoff-limited) | ~1.4e-2 |
| 40 | 30 | ~6e-15 | ~4e-15 |

## 2. Truncation tail — estimated from coefficient decay

`estimate_tail` reports a `TailKind` and, separately, a strict L1 number:

- `none`: every dropped coefficient is at the roundoff floor
  (`noise_floor * max|a|`). Then `sum_{k>m} |a_k|` is a **strict** bound on
  the dropped terms of the *finite* interpolant, since `|T_k(t)| <= 1` on
  `[-1,1]`.
- `geometric`: local contraction `|a_k| ~ C q^k` along the populated parity;
  the reported value is an **infinite geometric extrapolation and is a
  heuristic**, never marked strict.
- `algebraic`: `|a_k| ~ C k^-p` with `p < min_algebraic_p` (default 3):
  the signature of a non-smooth function (cusp, jump). The reported value is
  the observed finite L1 sum, descriptive only.
- `non_decaying`: coefficients do not decay at the chosen degree.

Decay is measured on coefficients above the roundoff floor, excluding the
endpoint-folded `a_n`, and on a single parity subsequence — otherwise
functions with only even (`|x|`) or only odd (`sign`) coefficients fake a
geometric contraction near degree `n`.

## 3. What is NOT claimed

Without an explicit smoothness assertion we do **not** promise a strict
global error bound for an infinite Chebyshev series:

- geometric extrapolation needs analyticity in a Bernstein ellipse, which is
  only accepted when the caller sets `smoothness_asserted = true`
  (`config/tolerance_smooth.conf`);
- a handful of measured residual points cannot exclude bad behaviour between
  them for a non-smooth function.

Consequences encoded in `assess`:

| situation | verdict | reason |
| --- | --- | --- |
| measured error > tolerance | `rejected` | `residual_exceeds_tolerance` |
| `|x|`-like algebraic decay, even if measured tolerance passes | `indeterminate` | `non_smooth_algebraic_decay` |
| unresolved / non-decaying coefficients | `indeterminate` | `non_decaying_coefficients` |
| smooth-looking tail, no evidence, no smoothness claim | `indeterminate` | `insufficient_independent_evidence` |
| evidence within tolerance | `accepted` | `none` |

`accepted` therefore means "the stated measured tolerance is supported by
independent evidence" — not "a mathematically uniform error bound holds".
