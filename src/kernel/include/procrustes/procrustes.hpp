#pragma once

// Algorithm kernel: weighted orthogonal Procrustes / similarity fitting for
// already paired point sets (no correspondence search is performed).
//
// Given pairs (p_i, q_i) and non-negative weights w_i it minimizes
//   sum_i w_i || s R p_i + t - q_i ||^2
// over orthogonal R, with det(R)=+1 in rotation-only mode or arbitrary sign
// when reflections are allowed, and over s >= 0 in similarity mode.

#include "procrustes/contracts.hpp"
#include "procrustes/logger.hpp"
#include "procrustes/report.hpp"
#include "procrustes/types.hpp"

namespace procrustes {

// Fit using the kernel. A logger may be attached to record request-correlated
// step traces; without one logging is skipped.
FitResult fit(const PointSet& ps, const FitConfig& cfg,
              const RequestContext& ctx = {},
              diag::Logger* logger = nullptr);

}  // namespace procrustes
