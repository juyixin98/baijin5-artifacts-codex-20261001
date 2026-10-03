// Finite-interval mapping for Chebyshev sampling.
//
// Numerical contract (see docs/numerical_contract.md):
//   * The interval [lo, hi] must be finite with lo < hi.
//   * The reference variable t lives in [-1, 1], with
//         x = midpoint + half_length * t,   t = (x - midpoint) / half_length.
//   * Closed Chebyshev nodes of the second kind are
//         t_j = cos(pi * j / n), j = 0..n   (t_0 = +1 -> hi, t_n = -1 -> lo)
//     so BOTH physical endpoints are sampled.
#ifndef CHEB_INTERVAL_HPP
#define CHEB_INTERVAL_HPP

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

#include <Eigen/Dense>

namespace cheb {

struct Interval {
  double lo;
  double hi;

  constexpr double midpoint() const { return 0.5 * (lo + hi); }
  constexpr double half_length() const { return 0.5 * (hi - lo); }
};

inline void check_interval(const Interval& iv) {
  if (!(std::isfinite(iv.lo) && std::isfinite(iv.hi))) {
    throw std::invalid_argument("interval endpoints must be finite");
  }
  if (!(iv.lo < iv.hi)) {
    throw std::invalid_argument("interval must satisfy lo < hi");
  }
}

// [-1,1] -> [lo,hi]
inline double map_from_reference(const Interval& iv, double t) {
  return iv.midpoint() + iv.half_length() * t;
}

// [lo,hi] -> [-1,1]
inline double map_to_reference(const Interval& iv, double x) {
  return (x - iv.midpoint()) / iv.half_length();
}

// Closed Chebyshev nodes of the second kind in the physical interval.
// Returned in descending physical order: hi first, lo last (j = 0..n).
inline Eigen::VectorXd chebyshev_nodes(const Interval& iv, int degree) {
  check_interval(iv);
  if (degree < 1) {
    throw std::invalid_argument("Chebyshev degree must be >= 1");
  }
  Eigen::VectorXd nodes(degree + 1);
  const double mid = iv.midpoint();
  const double half = iv.half_length();
  for (int j = 0; j <= degree; ++j) {
    const double t = std::cos(M_PI * static_cast<double>(j) / degree);
    nodes[j] = mid + half * t;
  }
  return nodes;
}

// Reference-space nodes t_j = cos(pi j/n), j = 0..n.
inline Eigen::VectorXd reference_nodes(int degree) {
  if (degree < 1) {
    throw std::invalid_argument("Chebyshev degree must be >= 1");
  }
  Eigen::VectorXd nodes(degree + 1);
  for (int j = 0; j <= degree; ++j) {
    nodes[j] = std::cos(M_PI * static_cast<double>(j) / degree);
  }
  return nodes;
}

}  // namespace cheb

#endif
