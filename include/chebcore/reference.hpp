// SPDX-License-Identifier: MIT
//
// Independent reference oracle. Nothing here calls the production kernel:
//   * exact_polynomial_coefficients: closed-form Chebyshev coefficients of
//     monomials x^d derived from T_k identities / affine change of variable,
//     evaluated with Boost::multiprecision cpp_dec_float_50;
//   * high_precision_lobatto_fit: direct 50-digit DCT sums;
//   * barycentric_lobatto_value: independent barycentric Lagrange evaluation
//     of the interpolating polynomial from raw samples (no DCT, no Clenshaw);
//   * abs_t_coefficients: closed-form analytic series of |t| on [-1,1].
#ifndef CHEBCORE_REFERENCE_HPP
#define CHEBCORE_REFERENCE_HPP

#include <boost/multiprecision/cpp_dec_float.hpp>

#include <cstddef>
#include <functional>
#include <string>
#include <vector>

#include "chebcore/numeric_contract.hpp"

namespace chebcore::reference {

using Real = boost::multiprecision::cpp_dec_float_50;

struct ExactCase {
  std::string name;
  Interval domain{};
  std::size_t monomial_degree{0};
  std::size_t fit_degree{0};
  // Ordinary-sum coefficients a_0..a_n for T_k on the reference interval.
  std::vector<Real> expected_ref_coeffs;
};

// Exact Chebyshev coefficients of x^d on [a,b], ordinary sum, degree n (>=d).
// Derived by mapping x = c + h t and expanding (c + h t)^d, then replacing
// powers t^m via 2^{1-m} sums of T_{m-2j}. Independent of the SUT.
std::vector<Real> monomial_coefficients(const Interval& domain,
                                        std::size_t degree_d,
                                        std::size_t fit_degree_n);

// Ready-made exact cases used across tests and fixtures.
std::vector<ExactCase> exact_polynomial_cases();

// Direct high-precision DCT-I coefficients from sampled values at the Lobatto
// grid. Values ordered j=0..n (t_0=1 ... t_n=-1).
std::vector<Real> high_precision_lobatto_fit(const std::vector<Real>& values,
                                             std::size_t n);

std::vector<Real> high_precision_sample(
    const std::function<Real(Real)>& ref_f, std::size_t n);

// Barycentric Lagrange evaluation at t of the polynomial through the Lobatto
// nodes with given values. Uses the closed-form Lobatto barycentric weights
// w_j = (-1)^j * delta_j, delta_0=delta_n=1/2, else 1. Independent path.
Real barycentric_lobatto_value(const std::vector<Real>& values,
                               std::size_t n, Real t);

// Analytic coefficients of |t| on [-1,1]:
//   |t| = 2/pi - (4/pi) sum_{k>=1} (-1)^k/(4k^2-1) T_{2k}(t).
// Returned as ordinary-sum a_0..a_{2K} (odd entries zero).
std::vector<Real> abs_t_coefficients(std::size_t half_degree_k);

// Convenience: convert multiprecision vector to double.
std::vector<double> as_double(const std::vector<Real>& v);

}  // namespace chebcore::reference

#endif
