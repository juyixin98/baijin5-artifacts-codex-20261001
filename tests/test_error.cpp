#include <cmath>

#include "chebcore/error_estimate.hpp"
#include "chebcore/kernel.hpp"
#include "test_framework.hpp"

using namespace chebcore;

TEST_CASE("error:node residual and holdout error are reported separately") {
  auto fit = fit_sample([](double x) { return std::exp(x); },
                        Interval{-1, 1}, 20);
  auto r = evaluate_fit(
      fit, [](double x) { return std::exp(x); }, 16,
      ErrorTolerances{1e-9, 1e-9, 10.0});
  REQUIRE(r.node_max_abs <= 1e-12);
  REQUIRE(r.holdout_max_abs <= 1e-9);
  REQUIRE(r.truncation_tail_l1 >= 0.0);
  // The two quantities must be independently computed numbers.
  REQUIRE(std::isfinite(r.node_max_abs));
  REQUIRE(std::isfinite(r.holdout_max_abs));
}

TEST_CASE("error:smooth function is ACCEPT at adequate degree") {
  auto fit = fit_sample([](double x) { return std::exp(x); },
                        Interval{-1, 1}, 24);
  auto r = evaluate_fit(fit, [](double x) { return std::exp(x); }, 20,
                        ErrorTolerances{1e-10, 1e-10, 10.0});
  REQUIRE_EQ(static_cast<int>(r.verdict), static_cast<int>(Verdict::Accept));
}

TEST_CASE("error:coarse smooth fit is REJECT with coherent tail") {
  // exp at n=4 is smooth but under-resolved; holdout error ~1e-3 while node
  // residual ~0. Tail (|a_4|) is of the same order, so this is an honest
  // "too coarse" rejection, not an alias.
  auto fit = fit_sample([](double x) { return std::exp(x); },
                        Interval{-1, 1}, 4);
  auto r = evaluate_fit(fit, [](double x) { return std::exp(x); }, 4,
                        ErrorTolerances{1e-10, 1e-6, 10.0});
  REQUIRE(r.node_max_abs <= 1e-12);
  REQUIRE(r.holdout_max_abs > 1e-6);
  REQUIRE_EQ(static_cast<int>(r.verdict), static_cast<int>(Verdict::Reject));
  REQUIRE(r.tail_consistent_with_holdout);
}

TEST_CASE("error:nonsmooth |x| at n=1 is INCONCLUSIVE (alias evidence)") {
  // Constant through two nodes? Use f(x)=x^2? No: use the exact pathological
  // probe f(t)=cos(2*? )... Instead: |t| sampled at n=1 yields samples [1,1],
  // i.e. the constant-1 polynomial. Nodes are reproduced EXACTLY while off-node
  // error is O(1); the coefficient tail is exactly 0, so no coefficient-based
  // tail can explain it -> INCONCLUSIVE, never a false Accept.
  auto fit = fit_sample([](double x) { return std::fabs(x); },
                        Interval{-1, 1}, 1);
  REQUIRE_ABS(fit.coeffs[0], 1.0, 1e-15);
  REQUIRE_ABS(fit.coeffs[1], 0.0, 1e-15);
  auto r = evaluate_fit(fit, [](double x) { return std::fabs(x); }, 2,
                        ErrorTolerances{1e-10, 1e-8, 10.0});
  REQUIRE(r.node_max_abs <= 1e-14);
  REQUIRE(r.holdout_max_abs > 1e-3);
  REQUIRE_ABS(r.truncation_tail_l1, 0.0, 1e-15);
  REQUIRE(!r.tail_consistent_with_holdout);
  REQUIRE_EQ(static_cast<int>(r.verdict),
             static_cast<int>(Verdict::Inconclusive));
}

TEST_CASE("error:nonsmooth |x| at adequate degree is REJECT (tail explains it)") {
  // At n=24 the |t| kink is visible off-node and the algebraic tail is of the
  // same size: a genuine, explainable failure, still not Accept. This pins the
  // boundary between Inconclusive (alias) and Reject (under-resolution).
  auto fit = fit_sample([](double x) { return std::fabs(x); },
                        Interval{-1, 1}, 24);
  auto r = evaluate_fit(fit, [](double x) { return std::fabs(x); }, 20,
                        ErrorTolerances{1e-10, 1e-6, 10.0});
  REQUIRE(r.holdout_max_abs > 1e-6);
  REQUIRE_EQ(static_cast<int>(r.verdict), static_cast<int>(Verdict::Reject));
  REQUIRE(r.tail_consistent_with_holdout);
}

TEST_CASE("error:truncation tail is pure coefficient L1 statement") {
  std::vector<double> a = {1.0, 0.0, 0.2, 0.0, -0.1, 0.0, 0.03};
  REQUIRE_ABS(truncation_tail_l1(a, 3), 0.13, 1e-15);
  REQUIRE_ABS(truncation_tail_l1(a, 7), 0.0, 1e-15);
  REQUIRE_THROWS_STD(std::invalid_argument, truncation_tail_l1(a, 8));
}

TEST_CASE("error:corrupted node residual yields REJECT not Accept") {
  // Hand-build a fit whose coefficients were tampered with after fitting:
  // node residual must be caught even if a lax holdout passed.
  auto fit = fit_sample([](double x) { return x * x; }, Interval{-1, 1}, 4);
  fit.coeffs[3] += 0.5;  // breaks node reconstruction
  auto r = evaluate_fit(fit, [](double x) { return x * x; }, 5,
                        ErrorTolerances{1e-8, 1.0, 10.0});
  REQUIRE(r.node_max_abs > 1e-8);
  REQUIRE_EQ(static_cast<int>(r.verdict), static_cast<int>(Verdict::Reject));
}

int main() { return chebtest::run_all(); }
