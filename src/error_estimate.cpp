#include "chebcore/error_estimate.hpp"

#include <algorithm>
#include <cmath>

namespace chebcore {

const char* to_string(Verdict v) {
  switch (v) {
    case Verdict::Accept:
      return "ACCEPT";
    case Verdict::Reject:
      return "REJECT";
    case Verdict::Inconclusive:
      return "INCONCLUSIVE";
  }
  return "UNKNOWN";
}

std::vector<double> default_holdout_points(std::size_t n) {
  // Midpoints of consecutive Lobatto cells in node order t_0=1 ... t_n=-1,
  // plus two deterministic fractions that never coincide with a node.
  auto nodes = lobatto_nodes(n);
  std::vector<double> pts;
  pts.reserve(n + 2);
  for (std::size_t j = 0; j + 1 < nodes.size(); ++j) {
    pts.push_back(0.5 * (nodes[j] + nodes[j + 1]));
  }
  pts.push_back(0.6180339887498948482);
  pts.push_back(-0.3819660112501051518);
  return pts;
}

ErrorReport evaluate_fit(const ChebFit& fit,
                         const std::function<double(double)>& physical_f,
                         std::size_t keep_terms, ErrorTolerances tol,
                         const std::vector<double>* holdout_t) {
  ErrorReport r;
  r.keep_terms = keep_terms;

  // --- (1) node residual: reconstruction at the sampling grid ----------------
  const auto xs = sample_nodes(fit.domain, fit.degree);
  for (std::size_t j = 0; j < xs.size(); ++j) {
    const double err = std::fabs(fit.eval_physical(xs[j]) - fit.sample_values[j]);
    r.node_max_abs = std::max(r.node_max_abs, err);
  }

  // --- (2) independent holdout error ----------------------------------------
  std::vector<double> storage;
  const std::vector<double>* pts = holdout_t;
  if (pts == nullptr) {
    storage = default_holdout_points(fit.degree);
    pts = &storage;
  }
  for (double t : *pts) {
    const double x = from_reference(t, fit.domain);
    double fx = std::numeric_limits<double>::quiet_NaN();
    try {
      fx = physical_f(x);
    } catch (...) {
      r.verdict = Verdict::Reject;
      r.reason = "holdout function threw; cannot evaluate error";
      return r;
    }
    if (!std::isfinite(fx)) {
      r.verdict = Verdict::Reject;
      r.reason = "holdout target non-finite; error undefined";
      return r;
    }
    r.holdout_max_abs =
        std::max(r.holdout_max_abs, std::fabs(fit.eval_reference(t) - fx));
  }

  // --- (3) coefficient truncation tail (algebraic, separate quantity) -------
  r.truncation_tail_l1 = truncation_tail_l1(fit.coeffs, keep_terms);
  r.tail_consistent_with_holdout =
      r.holdout_max_abs <=
      tol.alias_evidence_ratio * std::max(r.truncation_tail_l1, 1e-300);

  // --- decision policy -------------------------------------------------------
  if (!std::isfinite(r.node_max_abs) || !std::isfinite(r.holdout_max_abs)) {
    r.verdict = Verdict::Reject;
    r.reason = "non-finite computed error";
    return r;
  }
  if (r.node_max_abs > tol.node_tol) {
    r.verdict = Verdict::Reject;
    r.reason = "node residual exceeds tolerance (fit does not reproduce samples)";
    return r;
  }
  if (r.holdout_max_abs > tol.holdout_tol) {
    if (r.tail_consistent_with_holdout) {
      r.verdict = Verdict::Reject;
      r.reason =
          "off-node error exceeds tolerance and is consistent with the "
          "coefficient tail: truncation/fit genuinely too coarse";
    } else {
      r.verdict = Verdict::Inconclusive;
      r.reason =
          "nodes reproduce exactly but off-node error exceeds tolerance by more "
          "than the coefficient tail can explain: possible aliasing/undersampling; "
          "no uniform error bound can be asserted";
    }
    return r;
  }
  r.verdict = Verdict::Accept;
  r.reason =
      "node and holdout errors within tolerance; tail reported as a separate "
      "algebraic coefficient statement (not a global bound)";
  return r;
}

}  // namespace chebcore
