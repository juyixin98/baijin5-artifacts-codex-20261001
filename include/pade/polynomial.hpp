#pragma once
#include "pade/types.hpp"

namespace pade::poly {

// Horner evaluation.
Real eval(const Vector& a, Real x) noexcept;

// Remove coefficients that are zero relative to the largest coefficient.
// Always keeps at least one element; the constant coefficient is never removed.
Vector trim(const Vector& a, Real tol) noexcept;

// Polynomial long division a / b; returns {quotient, remainder}.
// Requires b non-empty and its leading coefficient non-zero.
struct DivResult { Vector quotient; Vector remainder; };
DivResult divide(const Vector& a, const Vector& b, Real tol);

// Residual norm ||a - b||_inf relative to max(1, ||b||_inf).
Real relativeInfNorm(const Vector& a, const Vector& b);

// Monic GCD via Euclid's algorithm for coefficient vectors in ascending order.
// tol is the relative coefficient threshold used to stop / declare exact divides.
Vector gcdMonic(Vector a, Vector b, Real tol, int& iterations);

// Scale a polynomial so its leading coefficient becomes 1.
Vector makeMonic(const Vector& a) noexcept;

// True if b divides a (relative remainder norm below tol).
bool divides(const Vector& a, const Vector& b, Real tol, Real* rel_rem = nullptr);

} // namespace pade::poly
