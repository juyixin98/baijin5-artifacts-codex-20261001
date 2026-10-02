#include "chebcore/kernel.hpp"

#include <cmath>

namespace chebcore {

namespace {
double endpoint_factor(std::size_t k, std::size_t n) {
  return (k == 0 || k == n) ? 1.0 : 2.0;  // c_k
}

ChebFit assemble(const Interval& domain, std::size_t n,
                 const std::vector<double>& values, FitOptions options) {
  if (!domain.valid()) {
    throw std::invalid_argument("fit: " + domain.invalid_reason());
  }
  if (n < 1) {
    throw std::invalid_argument("fit: degree n must be >= 1");
  }
  if (values.size() != n + 1) {
    throw std::invalid_argument(
        "fit: expected n+1 sample values ordered on the descending Lobatto grid");
  }
  if (options.reject_nonfinite_samples) {
    for (double y : values) {
      if (!std::isfinite(y)) {
        throw std::invalid_argument(
            "fit: non-finite sample value (NaN/Inf); interpolation is undefined");
      }
    }
  }

  // DCT-I with endpoint half weights:
  //   a_k = (c_k/n) * sum_{j=0}^{n}'' f_j cos(k*pi*j/n)
  // where '' weights j=0,n by 1/2.
  const double inv_n = 1.0 / static_cast<double>(n);
  std::vector<double> a(n + 1, 0.0);
  for (std::size_t k = 0; k <= n; ++k) {
    double s = 0.0;
    for (std::size_t j = 0; j <= n; ++j) {
      const double d_j = (j == 0 || j == n) ? 0.5 : 1.0;
      s += d_j * values[j] *
           std::cos(kPi * static_cast<double>(k) * static_cast<double>(j) * inv_n);
    }
    a[k] = endpoint_factor(k, n) * inv_n * s;
  }

  ChebFit fit;
  fit.domain = domain;
  fit.degree = n;
  fit.coeffs = std::move(a);
  fit.sample_values = values;
  return fit;
}
}  // namespace

ChebFit fit_sample(const std::function<double(double)>& f,
                   const Interval& domain, std::size_t n,
                   FitOptions options) {
  const auto xs = sample_nodes(domain, n);
  std::vector<double> ys(xs.size());
  for (std::size_t j = 0; j < xs.size(); ++j) ys[j] = f(xs[j]);
  return assemble(domain, n, ys, options);
}

ChebFit fit_values(const Interval& domain, std::size_t n,
                   const std::vector<double>& values, FitOptions options) {
  return assemble(domain, n, values, options);
}

double clenshaw(const std::vector<double>& a, double t) {
  if (a.empty()) {
    throw std::invalid_argument("clenshaw: empty coefficient vector");
  }
  if (!std::isfinite(t)) {
    throw std::invalid_argument("clenshaw: evaluation point must be finite");
  }
  const std::size_t n = a.size() - 1;
  double b_next = 0.0;  // b_{n+1}
  double b_cur = 0.0;   // b_n
  for (std::size_t k = n + 1; k-- > 0;) {
    const double b_prev = a[k] + 2.0 * t * b_cur - b_next;
    b_next = b_cur;
    b_cur = b_prev;
  }
  // sum = b_0 - t*b_1 (endpoint-safe; no a_n halving in storage).
  return b_cur - t * b_next;
}

double direct_cos_sum(const std::vector<double>& a, double t) {
  if (a.empty()) {
    throw std::invalid_argument("direct_cos_sum: empty coefficient vector");
  }
  if (!std::isfinite(t)) return std::numeric_limits<double>::quiet_NaN();
  const double theta = std::acos(std::clamp(t, -1.0, 1.0));
  double s = 0.0;
  for (std::size_t k = 0; k < a.size(); ++k) {
    s += a[k] * std::cos(static_cast<double>(k) * theta);
  }
  return s;
}

double truncation_tail_l1(const std::vector<double>& a, std::size_t keep) {
  if (keep > a.size()) {
    throw std::invalid_argument("truncation_tail_l1: keep_terms > coeff count");
  }
  double tail = 0.0;
  for (std::size_t k = keep; k < a.size(); ++k) tail += std::fabs(a[k]);
  return tail;
}

double ChebFit::eval_reference(double t) const {
  return clenshaw(coeffs, t);
}

double ChebFit::eval_physical(double x) const {
  return clenshaw(coeffs, to_reference(x, domain));
}

}  // namespace chebcore
