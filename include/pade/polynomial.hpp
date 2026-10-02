// Polynomial primitives (ascending-power coefficient convention).
#pragma once

#include <complex>
#include <vector>

#include "pade/types.hpp"

namespace pade::poly {

// Trim trailing coefficients whose magnitude is <= tol * max|coeff|.
// Never trims below length 1.
std::vector<Real> trim(const std::vector<Real>& p, Real rel_tol);

// c = a*b convolution, length a.size()+b.size()-1.
std::vector<Real> multiply(const std::vector<Real>& a,
                           const std::vector<Real>& b);

// Horner evaluation.
Real evaluate(const std::vector<Real>& p, Real x);

// Exact (integer-index) leading-zero count.
int leadingZeroShift(const std::vector<Real>& p, Real abs_tol);

// Divide polynomial a by monic divisor d using synthetic division.
// Returns quotient; requires remainder <= rel_tol (checked by caller).
std::vector<Real> divideByMonic(const std::vector<Real>& a,
                                const std::vector<Real>& d);

// Approximate monic polynomial GCD of a,b via Euclidean algorithm with
// relative pivot tolerance. Returns {gcd(monic), degree}. Degree 0 => {1.0}.
struct ApproxGcd {
    std::vector<Real> gcd;
    int degree = 0;
};
ApproxGcd approximateGcd(const std::vector<Real>& a,
                         const std::vector<Real>& b,
                         Real rel_tol);

// Companion-matrix roots for diagnostics/demo (not on core correctness path).
std::vector<std::complex<Real>> roots(const std::vector<Real>& p);

} // namespace pade::poly
