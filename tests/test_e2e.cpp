#include "test_framework.hpp"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

#include "cheb/config.hpp"
#include "cheb/errors.hpp"
#include "cheb/evaluate.hpp"
#include "cheb/expansion.hpp"

using namespace cheb;

static Func pick_func(const std::string& c) {
  if (c == "exp") return [](double x) { return std::exp(x); };
  if (c == "abs") return [](double x) { return std::fabs(x); };
  if (c == "sign")
    return [](double x) { return x < 0 ? -1.0 : (x > 0 ? 1.0 : 0.0); };
  if (c == "t4")
    return [](double x) { return x * x * x * x; };
  return nullptr;
}

static ErrorReport run_case(const std::string& cs, int degree, int trunc,
                            const ToleranceProfile& prof) {
  const Interval iv{-1, 1};
  const Func f = pick_func(cs);
  auto ex = cheb_fit(f, iv, degree);
  Eigen::VectorXd fr(801), tr(801);
  for (int i = 0; i < 801; ++i) {
    const double t = -1.0 + 2.0 * (i + 0.31) / (801.0 + 0.62);
    fr[i] = std::fabs(clenshaw(ex, t) - f(t));
    tr[i] = std::fabs(clenshaw_truncated(ex, trunc, t) - f(t));
  }
  return assess(ex.coeff, trunc, fr, tr, false, to_assess_options(prof));
}

TEST_CASE("e2e.smooth_exp_accepted_with_measured_evidence") {
  const auto p = load_profile("config/tolerance_strict.conf");
  const auto rep = run_case("exp", 40, 30, p);
  CHECK_TRUE(rep.verdict == Verdict::Accepted, "exp_accepted");
  CHECK_TRUE(rep.fit_residual < 1e-13, "exp_fit_residual_tiny");
}

TEST_CASE("e2e.abs_rejected_under_strict_tolerance") {
  const auto p = load_profile("config/tolerance_strict.conf");
  const auto rep = run_case("abs", 64, 64, p);
  CHECK_TRUE(rep.verdict == Verdict::Rejected, "abs_rejected_strict");
  CHECK_TRUE(rep.reason == FailureReason::ResidualExceedsTolerance,
             "abs_reject_category");
}

TEST_CASE("e2e.sign_discontinu_never_accepted_with_concrete_category") {
  const ToleranceProfile p{1e-2, false, 6, 1e-13, 3.0};
  const auto rep = run_case("sign", 64, 32, p);
  CHECK_TRUE(rep.verdict != Verdict::Accepted, "sign_never_accepted");
  CHECK_TRUE(rep.reason == FailureReason::NonDecayingCoefficients ||
                 rep.reason == FailureReason::NonSmoothAlgebraicDecay ||
                 rep.reason == FailureReason::ResidualExceedsTolerance,
             "sign_failure_category_concrete");
}

TEST_CASE("e2e.polynomial_t4_accepted_exact") {
  const ToleranceProfile p{1e-12, false, 6, 1e-13, 3.0};
  const auto rep = run_case("t4", 8, 8, p);
  CHECK_TRUE(rep.verdict == Verdict::Accepted, "t4_accepted");
  CHECK_TRUE(rep.tail.kind == TailKind::None, "t4_tail_none");
}

int main() { return chebtest::run_all(); }
