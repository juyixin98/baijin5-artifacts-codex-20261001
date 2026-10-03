#include "test_framework.hpp"

#include <cmath>

#include "cheb/errors.hpp"
#include "cheb/evaluate.hpp"
#include "cheb/expansion.hpp"

using namespace cheb;

static Eigen::VectorXd residuals(const ChebExpansion& ex, const Func& f,
                                 int trunc, int points) {
  Eigen::VectorXd r(points + 1);
  for (int i = 0; i <= points; ++i) {
    const double t = -1.0 + 2.0 * (i + 0.37) / (points + 0.74);
    const double x = map_from_reference(ex.interval, t);
    const double approx =
        trunc < ex.degree() ? clenshaw_truncated(ex, trunc, x) : clenshaw(ex, x);
    r[i] = std::fabs(approx - f(x));
  }
  return r;
}

TEST_CASE("errors.smooth_truncation_geometric_heuristic_is_not_strict") {
  // Degree 12, truncate at 6: dropped terms 7..12 still carry signal and
  // contract geometrically. At high degree the tail reaches the roundoff
  // floor and becomes a strict None (covered by the next case separately).
  auto ex = cheb_fit([](double t) { return std::exp(t); }, Interval{-1, 1},
                     12);
  const TailEstimate te = estimate_tail(ex.coeff, 6);
  CHECK_TRUE(te.kind == TailKind::Geometric, "smooth_tail_geometric");
  CHECK_TRUE(!te.is_strict_bound, "heuristic_must_not_be_labelled_strict");
  CHECK_TRUE(te.q > 0.01 && te.q < 0.7, "geometric_q_sane");
  CHECK_TRUE(te.value > 1e-6, "geometric_tail_magnitude_order");
}

TEST_CASE("errors.high_degree_tail_at_roundoff_floor_is_strict_none") {
  auto ex = cheb_fit([](double t) { return std::exp(t); }, Interval{-1, 1},
                     24);
  const TailEstimate te = estimate_tail(ex.coeff, 12);
  CHECK_TRUE(te.kind == TailKind::None, "floor_tail_none");
  CHECK_TRUE(te.is_strict_bound, "floor_l1_strict");
  CHECK_TRUE(te.value < 1e-12, "floor_tail_tiny");
}

TEST_CASE("errors.polynomial_tail_roundoff_floor_is_strict") {
  auto ex = cheb_fit([](double t) { return t * t; }, Interval{-1, 1}, 8);
  const TailEstimate te = estimate_tail(ex.coeff, 2);
  CHECK_TRUE(te.kind == TailKind::None, "poly_tail_none");
  CHECK_TRUE(te.is_strict_bound, "roundoff_floor_l1_strict");
}

TEST_CASE("errors.abs_tail_algebraic_and_verdict_indeterminate") {
  auto ex = cheb_fit([](double t) { return std::fabs(t); }, Interval{-1, 1},
                     64);
  const TailEstimate te = estimate_tail(ex.coeff, 16);
  CHECK_TRUE(te.kind == TailKind::Algebraic, "abs_tail_algebraic");
  CHECK_TRUE(!te.is_strict_bound, "abs_tail_not_strict");
  CHECK_TRUE(te.algebraic_p > 0.8 && te.algebraic_p < 2.5,
             "abs_algebraic_exponent_order1");

  const auto fit_r = residuals(ex, [](double t) { return std::fabs(t); }, 64, 400);
  const auto trunc_r = residuals(ex, [](double t) { return std::fabs(t); }, 16, 400);

  AssessOptions tight;
  tight.tolerance = 1e-12;
  const auto rep_tight =
      assess(ex.coeff, 16, fit_r, trunc_r, false, tight);
  CHECK_TRUE(rep_tight.verdict == Verdict::Rejected,
             "abs_tight_tol_rejected");
  CHECK_TRUE(rep_tight.reason == FailureReason::ResidualExceedsTolerance,
             "abs_reject_category");

  AssessOptions relaxed;
  relaxed.tolerance = 1.0;  // measured passes comfortably
  const auto rep_relaxed =
      assess(ex.coeff, 16, fit_r, trunc_r, false, relaxed);
  CHECK_TRUE(rep_relaxed.verdict == Verdict::Indeterminate,
             "abs_relaxed_indeterminate");
  CHECK_TRUE(rep_relaxed.reason == FailureReason::NonSmoothAlgebraicDecay,
             "abs_indeterminate_category");
}

TEST_CASE("errors.sign_function_unresolved_is_rejected_or_indeterminate") {
  auto ex = cheb_fit(
      [](double t) { return t < 0 ? -1.0 : (t > 0 ? 1.0 : 0.0); },
      Interval{-1, 1}, 64);
  const TailEstimate te = estimate_tail(ex.coeff, 8);
  CHECK_TRUE(te.kind == TailKind::Algebraic ||
                 te.kind == TailKind::NonDecaying,
             "sign_tail_not_geometric");

  const auto fit_r = residuals(
      ex, [](double t) { return t < 0 ? -1.0 : (t > 0 ? 1.0 : 0.0); }, 64, 400);
  const auto trunc_r = residuals(
      ex, [](double t) { return t < 0 ? -1.0 : (t > 0 ? 1.0 : 0.0); }, 8, 400);
  AssessOptions opt;
  opt.tolerance = 0.05;  // Gibbs-level truncation error is O(0.1)
  const auto rep = assess(ex.coeff, 8, fit_r, trunc_r, false, opt);
  CHECK_TRUE(rep.verdict != Verdict::Accepted, "sign_never_accepted");
  CHECK_TRUE(rep.reason == FailureReason::ResidualExceedsTolerance ||
                 rep.reason == FailureReason::NonDecayingCoefficients ||
                 rep.reason == FailureReason::NonSmoothAlgebraicDecay,
             "sign_concrete_failure_category");
}

TEST_CASE("errors.no_evidence_without_smoothness_is_indeterminate") {
  auto ex = cheb_fit([](double t) { return std::exp(t); }, Interval{-1, 1},
                     24);
  AssessOptions opt;
  opt.tolerance = 1e-10;
  const auto rep =
      assess(ex.coeff, 12, Eigen::VectorXd{}, Eigen::VectorXd{}, false, opt);
  CHECK_TRUE(rep.verdict == Verdict::Indeterminate,
             "no_evidence_indeterminate");
  CHECK_TRUE(rep.reason == FailureReason::InsufficientIndependentEvidence,
             "no_evidence_category");
}

TEST_CASE("errors.fit_error_and_truncation_error_measured_separately") {
  // Degree-8 fit to exp: full-degree interpolation error ~1e-9, truncation
  // to degree 2 is O(1e-2). The channels must stay separate.
  auto ex = cheb_fit([](double t) { return std::exp(t); }, Interval{-1, 1}, 8);
  const auto fit_r = residuals(ex, [](double t) { return std::exp(t); }, 8, 400);
  const auto trunc_r = residuals(ex, [](double t) { return std::exp(t); }, 2, 400);
  CHECK_TRUE(fit_r.maxCoeff() < 1e-7, "degree8_fit_small");
  CHECK_TRUE(trunc_r.maxCoeff() > 1e-3, "degree2_truncation_tangible");
  const TailEstimate te = estimate_tail(ex.coeff, 2);
  CHECK_TRUE(te.kind == TailKind::Geometric, "exp8_tail_geometric");
  CHECK_TRUE(te.value > 1e-3, "geometric_tail_same_order");
}

int main() { return chebtest::run_all(); }

TEST_CASE("errors.smoothness_asserted_turns_geometric_tail_into_bound") {
  auto ex = cheb_fit([](double t) { return std::exp(t); }, Interval{-1, 1},
                     12);
  // Degree-6 truncation: residual ~ O(1e-6), tail geometric.
  const auto fit_r = residuals(ex, [](double t) { return std::exp(t); }, 12, 400);
  const auto trunc_r = residuals(ex, [](double t) { return std::exp(t); }, 6, 400);
  AssessOptions opt;
  opt.tolerance = 1e-5;
  opt.smoothness_asserted = true;
  const auto rep = assess(ex.coeff, 6, fit_r, trunc_r, false, opt);
  CHECK_TRUE(rep.verdict == Verdict::Accepted, "asserted_smooth_accepted");
}
