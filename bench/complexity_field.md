# Complexity note (local synthetic data)

- domain: `FIELD` mod `1000000007`
- fitted wall-clock exponent, tree kernel: **1.845**; pointwise Horner baseline: **1.982**
- arithmetic: Karatsuba multiplication + Newton series inversion gives a sub-quadratic ~O(n^1.58) polynomial-op regime for the tree; pointwise Horner is ~O(n^2).

| n | tree s | Horner s | batches |
|---:|---:|---:|---:|
| 64 | 0.0006 | 0.0003 | 1 |
| 128 | 0.0027 | 0.0016 | 1 |
| 256 | 0.0083 | 0.0053 | 1 |
| 512 | 0.0307 | 0.0210 | 1 |
| 1024 | 0.1130 | 0.0814 | 1 |

## Interpretation
- OK: tree scaling is clearly sub-quadratic relative to Horner; no uncertainty.
