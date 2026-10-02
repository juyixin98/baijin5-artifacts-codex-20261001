#include <cmath>
#include <stdexcept>
#include <vector>

#include <Eigen/Dense>

#include "chebcore/eigen_crosscheck.hpp"
#include "chebcore/kernel.hpp"
#include "test_framework.hpp"

using namespace chebcore;

namespace {
// Hand-derived closed-form coefficients (ordinary sum) on [-1,1].
std::vector<double> expected_monomial(std::size_t degree,
                                      std::size_t n) {
  std::vector<double> a(n + 1, 0.0);
  auto put = [&](std::size_t k, double v) {
    if (k < a.size()) a[k] = v;
  };
  switch (degree) {
    case 0: put(0, 1.0); break;
    case 1: put(1, 1.0); break;
    case 2: put(0, 0.5); put(2, 0.5); break;
    // x^3 = (3 T1 + T3)/4
    case 3: put(1, 0.75); put(3, 0.25); break;
    // x^4 = (3 + 4 T2 + T4)/8
    case 4: put(0, 3.0/8); put(2, 0.5); put(4, 1.0/8); break;
    // x^5 = (10 T1 + 5 T3 + T5)/16
    case 5: put(1, 10.0/16); put(3, 5.0/16); put(5, 1.0/16); break;
    default: throw std::runtime_error("extend expected_monomial");
  }
  return a;
}
}  // namespace

TEST_CASE("kernel:monomials 0..5 get exact Chebyshev coefficients") {
  for (std::size_t d = 0; d <= 5; ++d) {
    const std::size_t n = std::max<std::size_t>(6, d);
    auto fit = fit_sample(
        [d](double x) { return std::pow(x, static_cast<double>(d)); },
        Interval{-1, 1}, n);
    auto want = expected_monomial(d, n);
    for (std::size_t k = 0; k <= n; ++k)
      REQUIRE_ABS(fit.coeffs[k], want[k], 1e-11);
  }
}

TEST_CASE("kernel:higher-degree coefficients vanish for degree-2 polynomial") {
  auto fit = fit_sample([](double x) { return x * x - 1; },
                        Interval{-1, 1}, 8);
  for (std::size_t k = 3; k <= 8; ++k)
    REQUIRE_ABS(fit.coeffs[k], 0.0, 1e-12);
}

TEST_CASE("kernel:constant interpolant at n=1 aliases higher modes yet is exact") {
  // Pathological undersampling probe: f=1 sampled at n=1 must give [1,0];
  // it reproduces constant everywhere, and must not masquerade as evidence
  // for any smoothness.
  auto fit = fit_sample([](double) { return 1.0; }, Interval{-1, 1}, 1);
  REQUIRE_ABS(fit.coeffs[0], 1.0, 1e-15);
  REQUIRE_ABS(fit.coeffs[1], 0.0, 1e-15);
  for (double t : {-1.0, -0.5, 0.2, 0.9, 1.0})
    REQUIRE_ABS(clenshaw(fit.coeffs, t), 1.0, 1e-15);
}

TEST_CASE("kernel:Clenshaw matches direct cosine sum at endpoints and interior") {
  auto fit = fit_sample([](double x) { return std::exp(x) * std::cos(2 * x); },
                        Interval{-1, 1}, 10);
  for (double t : {-1.0, -0.999, -0.37, 0.0, 0.41, 0.999, 1.0})
    REQUIRE_ABS(clenshaw(fit.coeffs, t), direct_cos_sum(fit.coeffs, t), 1e-11);
}

TEST_CASE("kernel:endpoint evaluations match sampled endpoint values") {
  auto fit = fit_sample([](double x) { return std::sin(2.1 * x) + 0.5; },
                        Interval{-2.0, 3.0}, 12);
  REQUIRE_ABS(fit.eval_physical(3.0), fit.sample_values.front(), 1e-12);
  REQUIRE_ABS(fit.eval_physical(-2.0), fit.sample_values.back(), 1e-12);
}

TEST_CASE("kernel:non-finite sample is rejected, not silently fitted") {
  REQUIRE_THROWS_STD(std::invalid_argument,
    fit_sample([](double x) { return x > 0.99 ? std::nan("") : x; },
               Interval{-1, 1}, 8));
  std::vector<double> bad(5, 1.0);
  bad[2] = std::numeric_limits<double>::infinity();
  REQUIRE_THROWS_STD(std::invalid_argument,
                     fit_values(Interval{-1, 1}, 4, bad));
}

TEST_CASE("kernel:wrong value count rejected") {
  REQUIRE_THROWS_STD(std::invalid_argument,
                     fit_values(Interval{-1, 1}, 4, std::vector<double>(3)));
}

TEST_CASE("kernel:Eigen DCT cross-check agrees with scalar kernel") {
  auto fit = fit_sample([](double x) { return std::tanh(3.0 * x); },
                        Interval{-1, 1}, 11);
  Eigen::Map<Eigen::VectorXd> f(fit.sample_values.data(),
                                static_cast<long>(fit.sample_values.size()));
  Eigen::VectorXd a = eigen_xcheck::dct1_coefficients(f);
  for (long k = 0; k < a.size(); ++k)
    REQUIRE_ABS(a[k], fit.coeffs[static_cast<std::size_t>(k)], 1e-12);
  // Exact reconstruction identity C a = f at the nodes.
  auto C = eigen_xcheck::lobatto_cosine_matrix(11);
  Eigen::VectorXd recon = C * a;
  for (long j = 0; j < recon.size(); ++j)
    REQUIRE_ABS(recon[j], f[j], 1e-12);
}

int main() { return chebtest::run_all(); }
