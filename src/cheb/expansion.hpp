// Chebyshev expansion coefficients from samples at the closed nodes.
//
// Coefficient convention: the stored vector holds the Chebyshev SERIES
// coefficients directly, i.e. the interpolant is
//   p(t) = a0 T0(t) + sum_{k=1}^{n} a_k T_k(t),
// and standard Clenshaw on the stored vector evaluates p. Endpoint weights are
// already folded in: the DCT-I trapezoidal rule gives 2*a_k at k=0,n, so its
// endpoint values are halved once; interior terms are unchanged.
//
//   raw_k = (2/n) * [ f0/2 + (-1)^k fn/2 + sum_{j=1}^{n-1} f_j cos(pi j k/n) ]
//   a_k = raw_k / 2 for k = 0,n ;  a_k = raw_k for 1 <= k <= n-1.
// (Getting this endpoint factor wrong by a factor of two is the classic
// silent failure; exact polynomial tests pin it.)
#ifndef CHEB_EXPANSION_HPP
#define CHEB_EXPANSION_HPP

#include <cmath>
#include <functional>
#include <stdexcept>

#include <Eigen/Dense>

#include "cheb/interval.hpp"

namespace cheb {

using Func = std::function<double(double)>;

struct ChebExpansion {
  Interval interval;
  int degree() const { return static_cast<int>(coeff.size()) - 1; }
  // Stored Chebyshev series coefficients (endpoint-folded).
  Eigen::VectorXd coeff;
};

// DCT-I Chebyshev coefficients from reference-space samples.
// samples[j] = f(t_j), t_j = cos(pi j/n), size n+1.
inline Eigen::VectorXd cheb_fit_reference(const Eigen::VectorXd& samples) {
  const int n = static_cast<int>(samples.size()) - 1;
  if (n < 1) {
    throw std::invalid_argument("need at least 2 samples (degree >= 1)");
  }
  Eigen::VectorXd coeff(n + 1);
  for (int k = 0; k <= n; ++k) {
    const double sign_n = (k % 2 == 0) ? 1.0 : -1.0;
    double sum = 0.5 * (samples[0] + sign_n * samples[n]);
    for (int j = 1; j < n; ++j) {
      sum += samples[j] * std::cos(M_PI * j * k / n);
    }
    sum *= 2.0 / n;
    if (k == 0 || k == n) {
      sum *= 0.5;  // DCT-I endpoint fold: recover the series coefficient
    }
    coeff[k] = sum;
  }
  return coeff;
}

// Sample a function on the closed nodes of iv and fit coefficients.
inline ChebExpansion cheb_fit(const Func& f, const Interval& iv, int degree) {
  check_interval(iv);
  if (degree < 1) {
    throw std::invalid_argument("Chebyshev degree must be >= 1");
  }
  Eigen::VectorXd samples(degree + 1);
  const Eigen::VectorXd nodes = chebyshev_nodes(iv, degree);
  for (int j = 0; j <= degree; ++j) {
    samples[j] = f(nodes[j]);
    if (!std::isfinite(samples[j])) {
      throw std::invalid_argument(
          "function returned a non-finite sample; finite-valued functions only");
    }
  }
  ChebExpansion result;
  result.interval = iv;
  result.coeff = cheb_fit_reference(samples);
  return result;
}

}  // namespace cheb

#endif
