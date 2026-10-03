# Numerical contract

## Interval mapping

`[lo, hi]` must be finite and satisfy `lo < hi` (anything else throws
`std::invalid_argument`):

```
x = midpoint + half_length * t,      midpoint = (lo + hi)/2
t = (x - midpoint) / half_length,    half_length = (hi - lo)/2
```

`t = -1` maps to `lo` and `t = +1` to `hi`.

## Closed Chebyshev nodes (both endpoints sampled)

```
t_j = cos(pi j / n),  j = 0..n        (t_0 = +1, t_n = -1)
x_j = midpoint + half_length * t_j
```

Unlike open nodes (Chebyshev roots), these include **both** physical
endpoints. The array is in descending physical order: `x_0 = hi`,
`x_n = lo`.

## Coefficients (DCT-I) and the endpoint factor

The interpolant is stored as Chebyshev **series** coefficients

```
p(t) = a0 T0(t) + sum_{k=1..n} a_k T_k(t)
```

and the standard Clenshaw recurrence applied to the stored vector evaluates
`p`. The discrete trapezoidal cosine rule returns `2 a_k` at the endpoints
`k = 0, n` and `a_k` in the interior, so exactly one endpoint halving is
applied to the raw DCT-I values:

```
raw_k = (2/n) [ f0/2 + (-1)^k fn/2 + sum_{j=1..n-1} f_j cos(pi j k/n) ]
a_k   = raw_k / 2  for k in {0,n};     a_k = raw_k for 1 <= k <= n-1
```

A missing or doubled factor of two here is the most common silent failure.
It is pinned by the exact identities

```
t^2 = (1/2) T0 + (1/2) T2
t^4 = (3/8) T0 + (1/2) T2 + (1/8) T4
```

in `tests/test_expansion.cpp` and `tests/fixtures/*.tsv`.

## Clenshaw recurrence

```
d_{n+1} = d_{n+2} = 0
d_k = 2 t d_{k+1} - d_{k+2} + a_k,   k = n..1
p(t)  = t d_1 - d_2 + a_0
```

Evaluating outside `[lo, hi]` is a mathematically defined polynomial
extrapolation, **not** an interpolation guarantee; callers should query
inside the interval.

Roundoff behaviour observed and tested: `t^4` at `n = 4` agrees everywhere
to ~1e-16 in double precision.

## Finite values only

Any non-finite sample (`NaN`, infinities) makes `cheb_fit` throw; finite
interval bounds and degree `n >= 1` are required.
