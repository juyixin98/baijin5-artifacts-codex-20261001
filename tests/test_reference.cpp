#include <boost/multiprecision/cpp_dec_float.hpp>

#include <cmath>
#include <functional>

#include "chebcore/kernel.hpp"
#include "chebcore/reference.hpp"
#include "test_framework.hpp"

using namespace chebcore;
using chebcore::reference::Real;

namespace {
double to_d(const Real& x) { return x.convert_to<double>(); }

double mp_max_rel(const std::vector<Real>& a, const std::vector<double>& b) {
  double m = 0;
  for (std::size_t k = 0; k < a.size(); ++k)
    m = std::max(m, std::fabs(to_d(a[k]) - b[k]) /
                        std::max(1.0, std::fabs(to_d(a[k]))));
  return m;
}
}  // namespace

TEST_CASE("reference:exact polynomial cases agree with scalar kernel") {
  for (const auto& c : reference::exact_polynomial_cases()) {
    auto fit = fit_sample(
        [&](double x) { return std::pow(x, static_cast<double>(c.monomial_degree)); },
        c.domain, c.fit_degree);
    // Analytic exact coefficients are independent of the SUT; compare in double.
    auto exact_d = reference::as_double(c.expected_ref_coeffs);
    REQUIRE_EQ(exact_d.size(), fit.coeffs.size());
    for (std::size_t k = 0; k < exact_d.size(); ++k) {
      const double tol =
          1e-12 * std::max(1.0, std::fabs(exact_d[k]));
      if (!(std::fabs(exact_d[k] - fit.coeffs[k]) <= tol))
        chebtest::fail("EXACT-COEFF-MISMATCH",
                       c.name + " k=" + std::to_string(k) + " exact=" +
                           std::to_string(exact_d[k]) + " kernel=" +
                           std::to_string(fit.coeffs[k]));
    }
  }
}

TEST_CASE("reference:hand constants x^2=[1/2,0,1/2], x^4=[3/8,0,1/2,0,1/8]") {
  auto a2 = reference::monomial_coefficients(Interval{-1, 1}, 2, 2);
  REQUIRE_ABS(to_d(a2[0]), 0.5, 1e-30);
  REQUIRE_ABS(to_d(a2[1]), 0.0, 1e-30);
  REQUIRE_ABS(to_d(a2[2]), 0.5, 1e-30);
  auto a4 = reference::monomial_coefficients(Interval{-1, 1}, 4, 4);
  REQUIRE_ABS(to_d(a4[0]), 3.0 / 8, 1e-30);
  REQUIRE_ABS(to_d(a4[2]), 0.5, 1e-30);
  REQUIRE_ABS(to_d(a4[4]), 1.0 / 8, 1e-30);
}

TEST_CASE("reference:affine interval x^2 on [2,4] coefficients") {
  // x = 3 + t; x^2 = 9 + 6t + t^2 = 19/2 + 6 T1 + 1/2 T2.
  auto a = reference::monomial_coefficients(Interval{2, 4}, 2, 3);
  REQUIRE_ABS(to_d(a[0]), 9.5, 1e-30);
  REQUIRE_ABS(to_d(a[1]), 6.0, 1e-30);
  REQUIRE_ABS(to_d(a[2]), 0.5, 1e-30);
  REQUIRE_ABS(to_d(a[3]), 0.0, 1e-30);
}

TEST_CASE("reference:50-digit DCT of exp matches scalar within round-off") {
  const std::size_t n = 20;
  std::function<Real(Real)> f = [](Real t) { return boost::multiprecision::exp(t); };
  auto vals = reference::high_precision_sample(f, n);
  auto a_mp = reference::high_precision_lobatto_fit(vals, n);
  auto fit = fit_sample([](double x) { return std::exp(x); },
                        Interval{-1, 1}, n);
  const double rel = mp_max_rel(a_mp, fit.coeffs);
  if (!(rel < 1e-13))
    chebtest::fail("MP-DCT-MISMATCH", "max rel = " + std::to_string(rel));
}

TEST_CASE("reference:barycentric oracle catches interpolation at holdouts") {
  // Independent evaluation path (no DCT/Clenshaw) for two functions; the
  // scalar Clenshaw evaluation must agree with barycentric Lagrange values.
  const std::size_t n = 16;
  std::function<Real(Real)> f1 = [](Real t) { return boost::multiprecision::sin(3 * t); };
  auto v1 = reference::high_precision_sample(f1, n);
  auto fit = fit_sample([](double x) { return std::sin(3 * x); },
                        Interval{-1, 1}, n);
  for (double t : {-0.99, -0.42, 0.01, 0.55, 0.97}) {
    Real want = reference::barycentric_lobatto_value(v1, n, Real(t));
    REQUIRE_ABS(fit.eval_reference(t), to_d(want), 1e-12);
  }
}

TEST_CASE("reference:barycentric exact-node path returns samples") {
  const std::size_t n = 8;
  std::function<Real(Real)> f = [](Real t) { return t * t * t; };
  auto v = reference::high_precision_sample(f, n);
  REQUIRE_ABS(to_d(reference::barycentric_lobatto_value(v, n, Real(1))),
              to_d(v.front()), 1e-30);
  REQUIRE_ABS(to_d(reference::barycentric_lobatto_value(v, n, Real(-1))),
              to_d(v.back()), 1e-30);
}

TEST_CASE("reference:|t| analytic series signs and magnitudes") {
  // |t| = 2/pi - (4/pi) sum_k (-1)^k/(4k^2-1) T_{2k}
  auto a = reference::abs_t_coefficients(4);
  const double pi = 3.14159265358979323846;
  REQUIRE_ABS(to_d(a[0]), 2.0 / pi, 1e-30);
  REQUIRE_ABS(to_d(a[2]), 4.0 / (3.0 * pi), 1e-14);  // k=1 positive
  REQUIRE(to_d(a[2]) > 0);
  REQUIRE_ABS(to_d(a[4]), 4.0 / (15.0 * pi), 1e-14); // a_4 (k=2) negative
  REQUIRE(to_d(a[4]) < 0);
  REQUIRE_ABS(to_d(a[6]), 4.0 / (35.0 * pi), 1e-14); // a_6 (k=3) positive
  REQUIRE(to_d(a[6]) > 0);
  // Partial series converges to |t| at off-node points.
  double s64 = 0;
  auto big = reference::abs_t_coefficients(64);
  for (std::size_t k = 0; k < big.size(); ++k)
    s64 += to_d(big[k]) * std::cos(static_cast<double>(k) * std::acos(0.37));
  REQUIRE_ABS(s64, 0.37, 1e-4);
}

TEST_CASE("reference:nonsmooth |t| converges only algebraically (max norm)") {
  // Empirically the Chebyshev interpolant of the Lipschitz-kink function |t|
  // has uniform error ~C/n (doubling n halves the error), with the worst point
  // at the kink t=0. This documents the algebraic regime and guards against
  // any code path that would imply spectral/exponential convergence. No
  // strict uniform bound is asserted from smoothness (there is none at 0).
  auto max_err = [&](std::size_t n) {
    auto fit = fit_sample([](double x) { return std::fabs(x); },
                          Interval{-1, 1}, n);
    double m = 0.0;
    for (int i = 0; i <= 4000; ++i) {
      const double t = -1.0 + 2.0 * i / 4000.0;
      m = std::max(m, std::fabs(fit.eval_reference(t) - std::fabs(t)));
    }
    return m;
  };
  const double e8 = max_err(8);
  const double e16 = max_err(16);
  const double e32 = max_err(32);
  // Worst point must be the kink neighbourhood.
  auto fit32 = fit_sample([](double x) { return std::fabs(x); },
                          Interval{-1, 1}, 32);
  REQUIRE(std::fabs(fit32.eval_reference(0.0)) > 1e-3);
  // Doubling n roughly halves the uniform error: O(1/n), not O(1/n^p, p>1).
  const double r1 = e8 / e16;
  const double r2 = e16 / e32;
  if (!(r1 > 1.5 && r1 < 3.0 && r2 > 1.5 && r2 < 3.0))
    chebtest::fail("DECAY-REGIME",
                   "expected O(1/n) algebraic decay, ratios=" +
                       std::to_string(r1) + "," + std::to_string(r2));
}

int main() { return chebtest::run_all(); }
