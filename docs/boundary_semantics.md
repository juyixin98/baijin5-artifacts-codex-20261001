# Boundary semantics

## Intervals

- `lo == hi`, `lo > hi`, `NaN`, or infinite endpoints are rejected before any
  sampling (`test_mapping.degenerate_and_bad_intervals_rejected`).
- Degrees `n < 1` are rejected; at least two closed nodes are required.
- Non-finite function samples are rejected (no silent NaN propagation).

## Endpoints of the interval

Closed nodes sample exactly `t = +1` and `t = -1`; Clenshaw at the endpoints
is asserted exact for the polynomial fixtures (`clenshaw.t4_matches_...`,
reference Runge endpoints test). Interpolation is node-wise exact even for a
cusp when it lands on a node (`|t|` at even `n` includes `t = 0`), while
mid-segment error near the cusp is explicitly asserted to remain nonzero.

## Non-smooth and discontinuous functions

- `|x|` (cusp at 0): interpolation error near the cusp is O(1/n); the fit is
  node-exact but is **not** globally small. Under a strict tolerance the
  verdict is `rejected`; under a relaxed tolerance it is `indeterminate`
  (algebraic coefficients, no uniform guarantee).
- `sign(t)` (jump): coefficients stay O(1/n) alternating; the tail is never
  geometric and the case can never be `accepted`.
- Smooth analytic functions (`exp`, `sin`, polynomials): geometric
  coefficient decay; at sufficient degree the dropped tail is a strict
  roundoff-floor L1 number for the finite interpolant.

## Precision guard

The independent reference runs in `long double` and checks at runtime that it
has at least ten extra mantissa bits over `double`. On platforms where that
fails, reference tests report `[ SKIP ]` rather than silently comparing
double against double.
