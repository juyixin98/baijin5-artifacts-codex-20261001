#include "test_framework.hpp"

#include <cmath>

#include "cheb/expansion.hpp"

using namespace cheb;

// Hand-derived exact coefficients (independent of any code path):
//   t^2 = (1/2) T0 + (1/2) T2
//   t^4 = (3/8) T0 + (1/2) T2 + (1/8) T4
// On [2,4], x = 3 + t: x^2 = 9.5 T0 + 6 T1 + 0.5 T2
TEST_CASE("expansion.exact_t2_coefficients") {
  auto ex = cheb_fit([](double t) { return t * t; }, Interval{-1, 1}, 2);
  CHECK_NEAR(ex.coeff[0], 0.5, 1e-15, "exact_t2_c0");
  CHECK_NEAR(ex.coeff[1], 0.0, 1e-15, "exact_t2_c1");
  CHECK_NEAR(ex.coeff[2], 0.5, 1e-15, "exact_t2_c2_endpoint_fold");
}

TEST_CASE("expansion.exact_t4_coefficients_endpoint_weights") {
  auto ex = cheb_fit([](double t) { return t * t * t * t; },
                     Interval{-1, 1}, 4);
  CHECK_NEAR(ex.coeff[0], 3.0 / 8.0, 1e-15, "exact_t4_c0");
  CHECK_NEAR(ex.coeff[1], 0.0, 1e-15, "exact_t4_c1");
  CHECK_NEAR(ex.coeff[2], 0.5, 1e-15, "exact_t4_c2");
  CHECK_NEAR(ex.coeff[3], 0.0, 1e-15, "exact_t4_c3");
  // Endpoint-weight regression: DCT-I endpoint fold must give the series
  // coefficient exactly (a common bug yields 1/16 here).
  CHECK_NEAR(ex.coeff[4], 1.0 / 8.0, 1e-15, "exact_t4_c4_endpoint_fold");
}

TEST_CASE("expansion.mapped_interval_x2_coefficients") {
  auto ex = cheb_fit([](double x) { return x * x; }, Interval{2.0, 4.0}, 2);
  CHECK_NEAR(ex.coeff[0], 9.5, 1e-13, "mapped_c0");
  CHECK_NEAR(ex.coeff[1], 6.0, 1e-13, "mapped_c1");
  CHECK_NEAR(ex.coeff[2], 0.5, 1e-13, "mapped_c2_endpoint_fold");
}

TEST_CASE("expansion.sin_and_abs_are_represented_not_garbage") {
  auto es = cheb_fit([](double t) { return std::sin(t); },
                     Interval{-1, 1}, 32);
  // sin(t) = 2 J1(1) T1 - 2 J3(1) T3 + ...
  CHECK_NEAR(es.coeff[1], 2.0 * 0.4400505857449335, 1e-12, "sin_c1");
  CHECK_NEAR(es.coeff[3], -2.0 * 0.0195633539826685, 1e-12, "sin_c3");

  auto ea = cheb_fit([](double t) { return std::fabs(t); },
                     Interval{-1, 1}, 64);
  // |t| = 2/pi + (4/pi) sum (-1)^k/(1-4k^2) T_{2k}; c2 = 4/(3pi).
  // Interpolated (aliased) coefficients at n=64 approximate the analytic
  // ones to O(1/n^2) ~ 2.4e-4; loose-but-concrete bounds catch sign/factor
  // errors without pretending the quadrature is exact for a non-smooth fn.
  CHECK_NEAR(ea.coeff[0], 2.0 / M_PI, 3e-3, "abs_c0");
  CHECK_NEAR(ea.coeff[2], 4.0 / (3.0 * M_PI), 3e-3, "abs_c2");
}

TEST_CASE("expansion.nonfinite_samples_rejected") {
  CHECK_THROWS_STD(
      cheb_fit([](double) { return std::numeric_limits<double>::quiet_NaN(); },
               Interval{-1, 1}, 4),
      "nonfinite_sample");
}

int main() { return chebtest::run_all(); }
