# Synthetic reference fixtures

All fixtures are hand-authored from exact Chebyshev identities, not generated
by the code under test:

- `polynomial_t4.tsv`: `8 t^4 = 3 T0 + 4 T2 + T4`.
- `polynomial_t2.tsv`: `2 t^2 = T0 + T2`.
- `mapped_x2.tsv`: on `[2,4]`, `x = 3 + t`, so `x^2 = 9.5 T0 + 6 T1 + 0.5 T2`.

Coefficients are in the series convention (`c_n` NOT halved). The core stores
the interior convention, so tests halve the last entry when comparing.

`tools/gen_fixture` regenerates a numeric long-double table for non-polynomial
cases (e.g. `exp`) into the build directory; those are cross-checks, never the
source of the exact answers above.
