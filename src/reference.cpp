#include "chebcore/reference.hpp"

#include <boost/multiprecision/cpp_dec_float.hpp>

#include <cmath>
#include <stdexcept>

namespace chebcore::reference {

namespace mp = boost::multiprecision;
namespace {
const Real& pi() {
  // Function-local to avoid static-initialization-order issues with the
  // cpp_dec_float backend (a namespace-scope Real initialized from acos can be
  // read before the backend constants are ready, yielding identical garbage).
  static const Real value = mp::acos(Real(-1));
  return value;
}

// Coefficients of t^m as an ordinary-sum Chebyshev series, length m+1.
// t^0 = T0. For m>=1: t^m = 2^{1-m} sum_{j=0}^{floor(m/2)}' C(m,j) T_{m-2j},
// where the prime halves the j=m/2 term when m is even.
std::vector<Real> monomial_ref_coeffs(std::size_t m) {
  std::vector<Real> c(m + 1, Real(0));
  if (m == 0) {
    c[0] = 1;
    return c;
  }
  Real scale = mp::pow(Real(2), static_cast<int>(1 - m));
  for (std::size_t j = 0; j <= m / 2; ++j) {
    Real binom = 1;
    for (std::size_t r = 0; r < j; ++r) {
      binom *= Real(m - r) / Real(r + 1);
    }
    Real term = scale * binom;
    if (2 * j == m) term /= 2;  // prime on the single even middle term
    c[m - 2 * j] += term;
  }
  return c;
}
}  // namespace

std::vector<Real> monomial_coefficients(const Interval& domain,
                                        std::size_t d, std::size_t n) {
  if (!domain.valid()) {
    throw std::invalid_argument("monomial_coefficients: invalid interval");
  }
  if (n < d) {
    throw std::invalid_argument("monomial_coefficients: require fit degree n >= d");
  }
  const Real center = (Real(domain.a) + Real(domain.b)) / 2;
  const Real half = (Real(domain.b) - Real(domain.a)) / 2;

  // Expand (c + h t)^d = sum_m C(d,m) c^{d-m} h^m t^m, then add each t^m's
  // exact Chebyshev coefficients.
  std::vector<Real> a(n + 1, Real(0));
  for (std::size_t m = 0; m <= d; ++m) {
    Real binom = 1;
    for (std::size_t r = 0; r < m; ++r)
      binom *= Real(d - r) / Real(r + 1);
    Real coef = binom * mp::pow(center, static_cast<int>(d - m)) *
                mp::pow(half, static_cast<int>(m));
    auto tc = monomial_ref_coeffs(m);
    for (std::size_t k = 0; k < tc.size(); ++k) a[k] += coef * tc[k];
  }
  return a;
}

std::vector<ExactCase> exact_polynomial_cases() {
  std::vector<ExactCase> cases;
  auto add = [&](std::string name, double a, double b, std::size_t d,
                 std::size_t n) {
    ExactCase c;
    c.name = std::move(name);
    c.domain = Interval{a, b};
    c.monomial_degree = d;
    c.fit_degree = n;
    c.expected_ref_coeffs = monomial_coefficients(c.domain, d, n);
    cases.push_back(std::move(c));
  };
  add("x^0 on [-1,1]", -1, 1, 0, 4);
  add("x^1 on [-1,1]", -1, 1, 1, 4);
  add("x^2 on [-1,1]", -1, 1, 2, 4);
  add("x^3 on [-1,1]", -1, 1, 3, 4);
  add("x^4 on [-1,1]", -1, 1, 4, 4);
  add("(x')^2 on [2,4]", 2, 4, 2, 4);
  add("(x')^3 on [-3,5]", -3, 5, 3, 6);
  add("(x')^5 on [0.5,2.0]", 0.5, 2.0, 5, 8);
  return cases;
}

std::vector<Real> high_precision_sample(
    const std::function<Real(Real)>& ref_f, std::size_t n) {
  std::vector<Real> v(n + 1);
  for (std::size_t j = 0; j <= n; ++j) {
    Real theta = pi() * Real(j) / Real(n);
    v[j] = ref_f(mp::cos(theta));
  }
  return v;
}

std::vector<Real> high_precision_lobatto_fit(const std::vector<Real>& f,
                                             std::size_t n) {
  if (f.size() != n + 1)
    throw std::invalid_argument("high_precision_lobatto_fit: need n+1 values");
  std::vector<Real> a(n + 1, Real(0));
  for (std::size_t k = 0; k <= n; ++k) {
    Real s = 0;
    for (std::size_t j = 0; j <= n; ++j) {
      Real dj = (j == 0 || j == n) ? Real(0.5) : Real(1);
      s += dj * f[j] *
           mp::cos(pi() * Real(k) * Real(j) / Real(n));
    }
    Real ck = (k == 0 || k == n) ? Real(1) : Real(2);
    a[k] = ck / Real(n) * s;
  }
  return a;
}

Real barycentric_lobatto_value(const std::vector<Real>& f, std::size_t n,
                               Real t) {
  if (f.size() != n + 1)
    throw std::invalid_argument("barycentric_lobatto_value: need n+1 values");
  // Exact node hit: return the sample (also avoids 0/0).
  for (std::size_t j = 0; j <= n; ++j) {
    Real tj = mp::cos(pi() * Real(j) / Real(n));
    if (t == tj) return f[j];
  }
  Real num = 0, den = 0;
  for (std::size_t j = 0; j <= n; ++j) {
    Real tj = mp::cos(pi() * Real(j) / Real(n));
    Real wj = (j % 2 == 0 ? Real(1) : Real(-1));
    if (j == 0 || j == n) wj /= 2;
    Real term = wj / (t - tj);
    num += term * f[j];
    den += term;
  }
  return num / den;
}

std::vector<Real> abs_t_coefficients(std::size_t K) {
  std::vector<Real> a(2 * K + 1, Real(0));
  a[0] = 2 / pi();
  for (std::size_t k = 1; k <= K; ++k) {
    Real rk(k);
    a[2 * k] = -(4 / pi()) * ((k % 2 == 0 ? Real(1) : Real(-1))) /
               (4 * rk * rk - 1);
  }
  return a;
}

std::vector<double> as_double(const std::vector<Real>& v) {
  std::vector<double> out;
  out.reserve(v.size());
  for (const auto& x : v) out.push_back(x.convert_to<double>());
  return out;
}

}  // namespace chebcore::reference
