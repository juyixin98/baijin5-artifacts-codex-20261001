# Complexity note (local synthetic data)

- domain: `INTEGER` mod `-`
- fitted wall-clock exponent, tree kernel: **2.508**; pointwise Horner baseline: **2.552**
- arithmetic: Karatsuba multiplication + Newton series inversion gives a sub-quadratic ~O(n^1.58) polynomial-op regime for the tree; pointwise Horner is ~O(n^2).
- exact integers: node coefficient bit width grows with n, so wall-clock exponent includes a bit-growth factor and the tree can be slower than Horner at moderate n; use FIELD for bounded digits where the contract allows it.

| n | tree s | Horner s | batches |
|---:|---:|---:|---:|
| 32 | 0.0007 | 0.0002 | 1 |
| 48 | 0.0017 | 0.0004 | 1 |
| 64 | 0.0033 | 0.0010 | 1 |
| 96 | 0.0115 | 0.0026 | 1 |
| 128 | 0.0214 | 0.0057 | 1 |

## Interpretation
- UNCERTAIN `TREE_NOT_FASTER_AT_SCALE`: tree wall exponent 2.508 is not clearly below Horner 2.552 (expected for exact integers at this scale: see bit-growth note).
