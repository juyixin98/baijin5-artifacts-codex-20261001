// Error accounting: sampling-fit error vs truncation-tail estimate.
//
// The two quantities are deliberately separate (docs/error_semantics.md):
//   * Sampling/fit error is MEASURED: residuals of BOTH the full-degree
//     interpolant (interpolation/rounding quality) and the truncated
//     polynomial (approximation quality at the kept degree), evaluated at
//     points independent of the fit nodes, preferably high precision
//     (see tools/bench_ref).
//   * Truncation tail is ESTIMATED from coefficient decay and labelled with
//     TailKind; a heuristic is never presented as a rigorous bound.
//
// No strict global error bound is asserted without an explicit smoothness
// assumption. Geometric extrapolation is heuristic; the strict L1 number
// covers only the finite interpolant's dropped coefficients.
#ifndef CHEB_ERRORS_HPP
#define CHEB_ERRORS_HPP

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>
#include <vector>

#include <Eigen/Dense>

namespace cheb {

enum class TailKind {
  None,         // dropped terms sit at the roundoff floor (or nothing dropped)
  Geometric,    // heuristic envelope |c_k| ~ C q^k, extended geometric sum
  Algebraic,    // observed |c_k| ~ C k^-p: non-smooth, descriptive only
  NonDecaying,  // no recognisable decay at this degree
};

enum class Verdict {
  Accepted,
  Indeterminate,
  Rejected,
};

enum class FailureReason {
  None,
  ResidualExceedsTolerance,
  NonSmoothAlgebraicDecay,
  NonDecayingCoefficients,
  InsufficientIndependentEvidence,
};

inline const char* to_string(TailKind k) {
  switch (k) {
    case TailKind::None: return "none";
    case TailKind::Geometric: return "geometric";
    case TailKind::Algebraic: return "algebraic";
    case TailKind::NonDecaying: return "non_decaying";
  }
  return "?";
}
inline const char* to_string(Verdict v) {
  switch (v) {
    case Verdict::Accepted: return "accepted";
    case Verdict::Indeterminate: return "indeterminate";
    case Verdict::Rejected: return "rejected";
  }
  return "?";
}
inline const char* to_string(FailureReason r) {
  switch (r) {
    case FailureReason::None: return "none";
    case FailureReason::ResidualExceedsTolerance:
      return "residual_exceeds_tolerance";
    case FailureReason::NonSmoothAlgebraicDecay:
      return "non_smooth_algebraic_decay";
    case FailureReason::NonDecayingCoefficients:
      return "non_decaying_coefficients";
    case FailureReason::InsufficientIndependentEvidence:
      return "insufficient_independent_evidence";
  }
  return "?";
}

struct TailEstimate {
  TailKind kind = TailKind::None;
  double value = 0.0;
  bool is_strict_bound = false;  // true only for the roundoff-floor L1 sum
  double q = 0.0;
  double envelope_c = 0.0;
  double algebraic_p = 0.0;
  double strict_l1 = 0.0;  // sum |c_k| of dropped terms of the finite fit
};

struct ErrorReport {
  double fit_residual = std::numeric_limits<double>::quiet_NaN();
  double truncation_residual = std::numeric_limits<double>::quiet_NaN();
  bool residual_is_independent = false;
  bool residual_is_high_precision = false;
  int residual_point_count = 0;
  TailEstimate tail{};
  Verdict verdict = Verdict::Indeterminate;
  FailureReason reason = FailureReason::None;
  std::string explanation;
};

struct TailOptions {
  int window = 6;
  double noise_floor = 1e-13;   // fraction of leading coefficient scale
  double geometric_q_max = 0.65;  // local ratio below this -> geometric
  double min_algebraic_p = 3.0;   // below: non-smooth -> indeterminate
};

// Stored coefficients are series coefficients (see expansion.hpp).
inline TailEstimate estimate_tail(const Eigen::VectorXd& coeff, int m,
                                  const TailOptions& opt = {}) {
  TailEstimate out;
  const int n = static_cast<int>(coeff.size()) - 1;
  if (m >= n) {
    out.kind = TailKind::None;
    out.value = 0.0;
    out.is_strict_bound = true;  // nothing dropped
    return out;
  }
  double strict_l1 = 0.0;
  for (int k = m + 1; k <= n; ++k) strict_l1 += std::abs(coeff[k]);
  out.strict_l1 = strict_l1;

  const double scale = coeff.cwiseAbs().maxCoeff();
  const double floor_level = opt.noise_floor * std::max(1.0, scale);

  // "Active" dropped indices: above the roundoff floor and excluding the
  // endpoint-folded degree n (its factor-of-two fold would bias log slopes).
  // Functions such as |x| have zero odd coefficients, so active indices keep
  // parity naturally and decay is measured along the populated subsequence.
  std::vector<int> active;
  for (int k = m + 1; k < n; ++k)
    if (std::abs(coeff[k]) > floor_level) active.push_back(k);

  if (active.empty()) {
    out.kind = TailKind::None;
    out.value = strict_l1;
    out.is_strict_bound = true;
    return out;
  }

  const int k_floor = active.back();

  // Same-parity subsequence: symmetric/antisymmetric functions populate only
  // one parity, and mixing parities near degree n creates a fake contraction.
  std::vector<int> same_parity;
  for (int k : active)
    if ((k % 2) == (k_floor % 2)) same_parity.push_back(k);
  const int k_first = same_parity.front();
  const int k_lo =
      same_parity[static_cast<size_t>(std::max<int>(
          0, static_cast<int>(same_parity.size()) - opt.window))];
  const double q = std::exp(
      (std::log(std::abs(coeff[k_floor])) -
       std::log(std::abs(coeff[k_lo]))) /
      static_cast<double>(k_floor - k_lo));

  double p = 0.0;
  if (k_floor - k_first >= 4) {
    p = -(std::log(std::abs(coeff[k_floor])) -
          std::log(std::abs(coeff[k_first]))) /
        (std::log(double(k_floor)) - std::log(double(k_first)));
    if (!std::isfinite(p)) p = 0.0;
  }

  // Rapid local contraction -> geometric envelope (smooth function).
  if (q < opt.geometric_q_max) {
    const double C = std::abs(coeff[k_lo]) / std::pow(q, k_lo);
    out.kind = TailKind::Geometric;
    out.q = q;
    out.envelope_c = C;
    out.value = C * std::pow(q, m + 1) / (1.0 - q);
    out.is_strict_bound = false;
    return out;
  }

  // Slow power-law decay -> non-smooth; descriptive only, never a bound.
  if (p > 0.0 && p < opt.min_algebraic_p) {
    out.kind = TailKind::Algebraic;
    out.algebraic_p = p;
    out.value = strict_l1;
    out.is_strict_bound = false;
    return out;
  }

  out.kind = TailKind::NonDecaying;
  out.value = strict_l1;
  out.is_strict_bound = false;
  return out;
}

struct AssessOptions {
  double tolerance = 1e-10;
  bool smoothness_asserted = false;
  TailOptions tail{};
};

// fit_residuals: |p_n(x)-f(x)| at independent points.
// trunc_residuals: |p_m(x)-f(x)| at independent points (approximation error).
inline ErrorReport assess(const Eigen::VectorXd& coeff, int truncate_degree,
                          const Eigen::VectorXd& fit_residuals,
                          const Eigen::VectorXd& trunc_residuals,
                          bool residuals_high_precision,
                          const AssessOptions& opt = {}) {
  ErrorReport rep;
  rep.tail = estimate_tail(coeff, truncate_degree, opt.tail);
  if (fit_residuals.size() > 0) {
    rep.fit_residual = fit_residuals.maxCoeff();
    rep.residual_point_count = static_cast<int>(fit_residuals.size());
    rep.residual_is_independent = true;
    rep.residual_is_high_precision = residuals_high_precision;
  }
  if (trunc_residuals.size() > 0)
    rep.truncation_residual = trunc_residuals.maxCoeff();

  const bool have_evidence = rep.residual_point_count >= 10;
  const double measured =
      std::max(std::isnan(rep.fit_residual) ? 0.0 : rep.fit_residual,
               std::isnan(rep.truncation_residual) ? 0.0
                                                    : rep.truncation_residual);

  if (have_evidence && measured > opt.tolerance) {
    rep.verdict = Verdict::Rejected;
    rep.reason = FailureReason::ResidualExceedsTolerance;
    rep.explanation =
        "measured max error " + std::to_string(measured) +
        " exceeds tolerance " + std::to_string(opt.tolerance) +
        " (fit=" + std::to_string(rep.fit_residual) +
        ", truncation=" + std::to_string(rep.truncation_residual) + ")";
    return rep;
  }

  if (rep.tail.kind == TailKind::Algebraic) {
    rep.verdict = Verdict::Indeterminate;
    rep.reason = FailureReason::NonSmoothAlgebraicDecay;
    rep.explanation =
        "coefficients decay algebraically (p ~ " +
        std::to_string(rep.tail.algebraic_p) +
        "): function lacks sufficient smoothness; measured points are finite "
        "evidence only and no strict global error bound follows without a "
        "smoothness hypothesis";
    return rep;
  }

  if (rep.tail.kind == TailKind::NonDecaying) {
    rep.verdict = Verdict::Indeterminate;
    rep.reason = FailureReason::NonDecayingCoefficients;
    rep.explanation =
        "coefficients do not decay at the truncation degree: unresolved "
        "function or degree too low; cannot certify";
    return rep;
  }

  if (rep.tail.kind == TailKind::Geometric && !opt.smoothness_asserted) {
    if (!have_evidence) {
      rep.verdict = Verdict::Indeterminate;
      rep.reason = FailureReason::InsufficientIndependentEvidence;
      rep.explanation =
          "geometric tail heuristic exists but analyticity in a Bernstein "
          "ellipse is not asserted and no independent residuals supplied; "
          "cannot certify a global error bound";
      return rep;
    }
  }

  if (!have_evidence) {
    rep.verdict = Verdict::Indeterminate;
    rep.reason = FailureReason::InsufficientIndependentEvidence;
    rep.explanation =
        "no independent residual points supplied; refusing to certify from "
        "fit coefficients alone";
    return rep;
  }

  rep.verdict = Verdict::Accepted;
  rep.reason = FailureReason::None;
  rep.explanation =
      "measured residual " + std::to_string(measured) +
      " within tolerance " + std::to_string(opt.tolerance) +
      (rep.tail.is_strict_bound
           ? "; dropped terms at roundoff floor, L1 is a strict bound on the "
             "finite interpolant"
           : "; geometric tail is a heuristic, not a strict global bound");
  return rep;
}

}  // namespace cheb

#endif
