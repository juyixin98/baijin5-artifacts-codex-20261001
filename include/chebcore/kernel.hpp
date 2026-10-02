// SPDX-License-Identifier: MIT
//
// Algorithm core: Chebyshev-Lobatto sampling, DCT-I coefficient fit, and
// Clenshaw evaluation of the resulting degree-n Chebyshev series.
#ifndef CHEBCORE_KERNEL_HPP
#define CHEBCORE_KERNEL_HPP

#include <cstddef>
#include <functional>
#include <stdexcept>
#include <vector>

#include "chebcore/numeric_contract.hpp"

namespace chebcore {

// Coefficient convention (documented, not implicit):
//   p(t) = sum_{k=0}^{n} a_k T_k(t)          (ordinary sum)
// a_0 and a_n are *not* halved in storage. The DCT formula carries the
// endpoint half weights internally (d_0 = d_n = 1/2, c_0 = c_n = 1).
struct ChebFit {
  Interval domain{};
  std::size_t degree{0};               // n
  std::vector<double> coeffs;          // a_0..a_n (length n+1), ordinary sum
  std::vector<double> sample_values;   // f at the Lobatto nodes, j=0..n

  double eval_physical(double x) const;
  double eval_reference(double t) const;
};

struct FitOptions {
  bool reject_nonfinite_samples = true;
};

// Sample f on the (n+1)-point Chebyshev-Lobatto grid of [a,b] and form the
// degree-n interpolating Chebyshev coefficients by the type-I DCT sum.
// Throws std::invalid_argument for bad intervals / n, and (by default) when a
// sampled value is non-finite, because such data cannot define a polynomial.
ChebFit fit_sample(const std::function<double(double)>& f,
                   const Interval& domain, std::size_t degree_n,
                   FitOptions options = {});

// Variant that accepts externally supplied sample values (e.g. fixtures).
// Values must be ordered on the descending Lobatto grid x_0=b ... x_n=a.
ChebFit fit_values(const Interval& domain, std::size_t degree_n,
                   const std::vector<double>& values,
                   FitOptions options = {});

// Clenshaw recurrence for sum_{k=0}^{n} a_k T_k(t). Valid for any t, but the
// meaning of the polynomial outside [-1,1] is extrapolation, not interpolation.
double clenshaw(const std::vector<double>& coeffs, double t);

// Direct cosine summation of the same series (independent recurrence path;
// used as a cross-check in tests, never to "prove" itself).
double direct_cos_sum(const std::vector<double>& coeffs, double t);

// Deterministic truncation-tail magnitude for the algebraic Chebyshev series:
//   sum_{k=K}^{n} |a_k|  (a uniform algebraic bound for |t| <= 1, since
// |T_k(t)| <= 1). This bounds truncation of the *fitted coefficients only*;
// it is not, without smoothness/convergence assumptions, a uniform bound on
// the true approximation error f - p_K.
double truncation_tail_l1(const std::vector<double>& coeffs,
                          std::size_t keep_terms);

}  // namespace chebcore

#endif
