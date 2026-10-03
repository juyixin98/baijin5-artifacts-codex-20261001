// Clenshaw evaluation of a Chebyshev expansion.
//
// For stored interior-convention coefficients the recurrence is
//   d_{n+1} = d_{n+2} = 0
//   d_k = 2 t d_{k+1} - d_{k+2} + a_k,  k = n .. 1
//   p(t) = t d_1 - d_2 + a_0
// evaluated in reference variable t, mapped from physical x.
#ifndef CHEB_EVALUATE_HPP
#define CHEB_EVALUATE_HPP

#include <Eigen/Dense>

#include "cheb/expansion.hpp"
#include "cheb/interval.hpp"

namespace cheb {

// Clenshaw recurrence in reference variable.
inline double clenshaw_reference(const Eigen::VectorXd& coeff, double t) {
  const int n = static_cast<int>(coeff.size()) - 1;
  double d1 = 0.0;  // d_{k+1}
  double d2 = 0.0;  // d_{k+2}
  for (int k = n; k >= 1; --k) {
    const double d = 2.0 * t * d1 - d2 + coeff[k];
    d2 = d1;
    d1 = d;
  }
  return t * d1 - d2 + coeff[0];
}

// Evaluate at a physical point x inside [lo, hi] (extrapolation outside the
// interval is mathematically well defined by the recurrence but is NOT an
// interpolation guarantee; callers are expected to query inside the interval).
inline double clenshaw(const ChebExpansion& ex, double x) {
  const double t = map_to_reference(ex.interval, x);
  return clenshaw_reference(ex.coeff, t);
}

// Vectorised Clenshaw over reference points.
inline Eigen::VectorXd clenshaw_reference(const Eigen::VectorXd& coeff,
                                          const Eigen::VectorXd& ts) {
  Eigen::VectorXd out(ts.size());
  for (int i = 0; i < ts.size(); ++i) {
    out[i] = clenshaw_reference(coeff, ts[i]);
  }
  return out;
}

inline Eigen::VectorXd clenshaw(const ChebExpansion& ex,
                                const Eigen::VectorXd& xs) {
  Eigen::VectorXd out(xs.size());
  for (int i = 0; i < xs.size(); ++i) {
    out[i] = clenshaw(ex, xs[i]);
  }
  return out;
}


// Clenshaw evaluation of the degree-m TRUNCATION of an expansion.
inline double clenshaw_truncated_reference(const Eigen::VectorXd& coeff,
                                           int m, double t) {
  Eigen::VectorXd truncated(m + 1);
  for (int k = 0; k <= m; ++k) truncated[k] = coeff[k];
  return clenshaw_reference(truncated, t);
}

inline double clenshaw_truncated(const ChebExpansion& ex, int m, double x) {
  const double t = map_to_reference(ex.interval, x);
  return clenshaw_truncated_reference(ex.coeff, m, t);
}

}  // namespace cheb

#endif
