// SPDX-License-Identifier: MIT
//
// Error interpretation. Three numerically distinct quantities are reported
// separately and must never be conflated:
//
//   * node_residual: measured error at the interpolation (sample) nodes. For a
//     degree-n fit on n+1 Lobatto nodes this is round-off by construction;
//     it says nothing about off-node accuracy.
//   * holdout_error: measured error at independent points NOT used in the fit.
//     This is the empirical sampling/fit error on the tested points.
//   * truncation_tail_l1: sum |a_k| over dropped coefficients, a uniform
//     algebraic bound on truncating the *fitted* series for |t|<=1. It is NOT
//     a bound on ||f - p_K|| unless extra smoothness + coefficient convergence
//     is supplied; this module makes no such claim.
#ifndef CHEBCORE_ERROR_ESTIMATE_HPP
#define CHEBCORE_ERROR_ESTIMATE_HPP

#include <cstddef>
#include <functional>
#include <string>
#include <vector>

#include "chebcore/kernel.hpp"

namespace chebcore {

enum class Verdict {
  Accept,        // measured node and off-node errors both within tolerance
  Reject,        // a hard violation: bad data, node residual, or holdout error
  Inconclusive   // evidence conflicts (e.g. perfect nodes but bad off-node)
};

const char* to_string(Verdict v);

struct ErrorTolerances {
  double node_tol = 1e-10;
  double holdout_tol = 1e-10;
  // Off-node residual larger than this multiple of the coefficient tail is
  // evidence that the tail (an algebraic coefficient statement) does not
  // describe the observed error; used to flag alias/undersampling situations.
  double alias_evidence_ratio = 10.0;
};

struct ErrorReport {
  Verdict verdict{Verdict::Inconclusive};
  double node_max_abs{0.0};
  double holdout_max_abs{0.0};
  double truncation_tail_l1{0.0};
  std::size_t keep_terms{0};
  std::string reason;
  // True only when the observed off-node error is consistent with the
  // algebraic coefficient tail at the configured ratio. This is a consistency
  // diagnostic, never a strict uniform bound on the unknown function.
  bool tail_consistent_with_holdout{false};
};

// Independent evaluation points on [-1,1], chosen to avoid the sampling nodes
// (midpoints of Lobatto cells plus deterministic off-grid fractions).
std::vector<double> default_holdout_points(std::size_t degree_n);

// Evaluate a fit against f at (a) its own nodes and (b) independent holdout
// points. `keep_terms = K+1` sets the truncation split a_0..a_K vs tail.
ErrorReport evaluate_fit(
    const ChebFit& fit, const std::function<double(double)>& physical_f,
    std::size_t keep_terms, ErrorTolerances tol = {},
    const std::vector<double>* holdout_reference_points = nullptr);

}  // namespace chebcore

#endif
