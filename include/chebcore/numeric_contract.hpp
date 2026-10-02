// SPDX-License-Identifier: MIT
//
// Numeric contract: finite-interval <-> [-1,1] mapping and Chebyshev-Lobatto
// sampling nodes. Everything in this file is ordinary double precision; the
// independent high-precision reference lives in reference.hpp.
#ifndef CHEBCORE_NUMERIC_CONTRACT_HPP
#define CHEBCORE_NUMERIC_CONTRACT_HPP

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <stdexcept>
#include <vector>
#include <string>

namespace chebcore {

// A closed finite interval [a, b]. Degenerate or reversed intervals are a
// caller error and are rejected explicitly (they never silently map).
struct Interval {
  double a{};
  double b{};

  bool valid() const noexcept {
    return std::isfinite(a) && std::isfinite(b) && b > a;
  }
  double length() const noexcept { return b - a; }
  double center() const noexcept { return 0.5 * (a + b); }
  std::string invalid_reason() const;
};

inline constexpr double kPi = 3.141592653589793238462643383279502884;

// Affine map x in [a,b] -> t in [-1,1].
//   t = (2x - a - b)/(b - a)
inline double to_reference(double x, const Interval& domain) {
  if (!domain.valid()) {
    throw std::invalid_argument("to_reference: " + domain.invalid_reason());
  }
  return (2.0 * x - (domain.a + domain.b)) / (domain.b - domain.a);
}

// Affine map t in [-1,1] -> x in [a,b].
inline double from_reference(double t, const Interval& domain) {
  if (!domain.valid()) {
    throw std::invalid_argument("from_reference: " + domain.invalid_reason());
  }
  if (!std::isfinite(t)) {
    throw std::invalid_argument("from_reference: evaluation point must be finite");
  }
  return domain.center() + 0.5 * domain.length() * t;
}

// Chebyshev points of the 2nd kind (Chebyshev-Lobatto nodes) on [-1,1]:
//   t_j = cos(pi*j/n),  j = 0..n  (descending; t_0 = 1, t_n = -1).
// The two endpoints are included exactly: this is the sampling grid for which
// the DCT-I coefficients below reproduce sampled values with zero residual at
// the nodes. n >= 1 is required (one point cannot carry endpoint weights).
inline std::vector<double> lobatto_nodes(std::size_t degree_n) {
  if (degree_n < 1) {
    throw std::invalid_argument(
        "lobatto_nodes: degree n must be >= 1 so both endpoints are sampled");
  }
  std::vector<double> nodes(degree_n + 1);
  const double inv_n = 1.0 / static_cast<double>(degree_n);
  for (std::size_t j = 0; j <= degree_n; ++j) {
    nodes[j] = std::cos(kPi * static_cast<double>(j) * inv_n);
  }
  nodes.front() = 1.0;
  nodes.back() = -1.0;
  return nodes;
}

// Corresponding physical sampling nodes on [a,b].
inline std::vector<double> sample_nodes(const Interval& domain,
                                        std::size_t degree_n) {
  auto ref = lobatto_nodes(degree_n);
  std::vector<double> physical(ref.size());
  std::transform(ref.begin(), ref.end(), physical.begin(),
                 [&](double t) { return from_reference(t, domain); });
  return physical;
}

// Clenshaw-Curtis quadrature weights on [-1,1] for the same Lobatto grid.
// Returned in the j=0..n (descending node) convention. The two endpoints carry
// half the weight of interior nodes in the composite rule; this routine returns
// the exact closed-form weights rather than a trapezoidal approximation.
std::vector<double> clenshaw_curtis_weights(std::size_t degree_n);

}  // namespace chebcore

#endif
