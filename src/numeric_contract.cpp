#include "chebcore/numeric_contract.hpp"

#include <string>

namespace chebcore {

std::string Interval::invalid_reason() const {
  if (!std::isfinite(a) || !std::isfinite(b)) {
    return "interval endpoints must be finite";
  }
  if (b <= a) {
    return "interval must satisfy b > a (degenerate/reversed intervals rejected)";
  }
  return "";
}

// Closed-form Clenshaw-Curtis weights for the Lobatto grid t_j = cos(pi j/n),
// j=0..n. Derived independently of the DCT coefficient code by integrating
// the cosine interpolant:
//
//   w_j = d_j * (2/n) * [ 1
//       - sum_{m : 2m < n} 2 cos(2 m theta_j)/(4m^2 - 1)
//       - (n even)        cos(n theta_j)/(n^2 - 1) ],
//
// where d_0 = d_n = 1/2 and d_j = 1 otherwise. The endpoint nodes therefore
// carry half the quadrature weight of interior nodes in the underlying cosine
// sum; the bracket restores the exact closed-form CC weights
// (n=1 -> [1,1], n=2 -> [1/3, 4/3, 1/3]).
std::vector<double> clenshaw_curtis_weights(std::size_t n) {
  if (n < 1) {
    throw std::invalid_argument("clenshaw_curtis_weights: n must be >= 1");
  }
  std::vector<double> w(n + 1, 0.0);
  const double inv_n = 1.0 / static_cast<double>(n);
  for (std::size_t j = 0; j <= n; ++j) {
    const double theta = kPi * static_cast<double>(j) * inv_n;
    double bracket = 1.0;
    for (std::size_t m = 1; 2 * m < n; ++m) {
      const double dm = static_cast<double>(m);
      bracket -= 2.0 * std::cos(2.0 * dm * theta) / (4.0 * dm * dm - 1.0);
    }
    if (n % 2 == 0) {
      const double dn = static_cast<double>(n);
      bracket -= std::cos(dn * theta) / (dn * dn - 1.0);
    }
    const double d_j = (j == 0 || j == n) ? 0.5 : 1.0;
    w[j] = d_j * 2.0 * inv_n * bracket;
  }
  return w;
}

}  // namespace chebcore
