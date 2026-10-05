#pragma once

// Algorithm kernel: numerical construction of Gauss-Legendre quadrature
// rules on the reference interval [-1, 1].
//
// Method: Newton refinement of the roots of the Legendre polynomial P_n,
// seeded with the Tricomi initial guess, evaluating P_n and P'_n by the
// three-term recurrence. Symmetry is exploited: only the roots in (0, 1)
// are iterated, the rest are mirrored, so the returned rule is symmetric
// bit-for-bit. Weights use w_i = 2 / ((1 - x_i^2) * P'_n(x_i)^2).
//
// Failure contract (Result<GaussRule>):
//   order == 0                       -> kInvalidInput
//   order > options.max_order        -> kResourceExhaustion
//   options inconsistent             -> kInvalidInput
//     (max_iterations == 0, negative convergence_tolerance, max_order == 0)
//   allocation failure               -> kResourceExhaustion
//   a root fails to converge within max_iterations, lands outside (-1, 1),
//   or yields a non-positive/non-finite weight
//                                    -> kComputationFailure; NO partial rule
//                                       is returned, diagnostics carry the
//                                       root index, iteration count, last
//                                       step size and residual
//
// Success invariants (checked by the test-suite):
//   nodes strictly ascending in (-1, 1), nodes[i] == -nodes[n-1-i] exactly,
//   weights[i] == weights[n-1-i] exactly, every weight > 0, sum(weights) ~ 2.

#include "gauss/rule.hpp"

namespace gauss {

class LegendreSolver {
 public:
  explicit LegendreSolver(SolverOptions options = {}) : options_(options) {}

  Result<GaussRule> compute(std::size_t order) const;

  const SolverOptions& options() const noexcept { return options_; }

 private:
  SolverOptions options_;
};

}  // namespace gauss
