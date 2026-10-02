#pragma once

#include "procrustes/contract.hpp"

namespace procrustes {

// Weighted orthogonal / similarity Procrustes fit of already paired points.
//
// Solves (depending on mode):
//   min over R(, s),t   sum_i w_i || q_i - s R p_i - t ||^2
// where R is orthogonal, proper (det +1) or reflection-allowed, and s is
// either fixed at 1 or a fitted non-negative uniform scale.
//
// No correspondence search is performed: columns of source/target are assumed
// to be matched a priori.
FitResult fit(const Request& request);

}  // namespace procrustes
