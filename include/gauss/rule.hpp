#pragma once

// Data contract for a Gauss-Legendre quadrature rule.
//
// A GaussRule is an immutable value: nodes (strictly ascending) and weights
// on a finite interval [lower, upper]. Rules on the reference interval
// [-1, 1] are produced by LegendreSolver; GaussRule::map_to affinely maps a
// rule onto any other finite interval.
//
// A default-constructed GaussRule is *empty*. Every derived operation on an
// empty rule fails with ErrorCategory::kStateConflict - this is how the
// library surfaces "you asked for something the current state cannot give".

#include <cstddef>
#include <limits>
#include <vector>

#include "gauss/error.hpp"

namespace gauss {

// Tunables for LegendreSolver. Each field maps to a distinct failure
// category when violated; see LegendreSolver's contract in legendre.hpp.
struct SolverOptions {
  std::size_t max_order = 512;       // hard cap on the number of nodes
  std::size_t max_iterations = 100;  // Newton iteration cap per root
  // Step tolerance for the Newton iteration, relative to |x| (floored at 1).
  // 0 selects default_convergence_tolerance(); negative is rejected.
  double convergence_tolerance = 0.0;
};

double default_convergence_tolerance() noexcept;

class GaussRule {
 public:
  GaussRule() = default;  // empty rule

  std::size_t size() const noexcept { return nodes_.size(); }
  bool empty() const noexcept { return nodes_.empty(); }

  const std::vector<double>& nodes() const noexcept { return nodes_; }
  const std::vector<double>& weights() const noexcept { return weights_; }

  // Interval bounds; NaN for an empty rule.
  double interval_lower() const noexcept { return lower_; }
  double interval_upper() const noexcept { return upper_; }

  // Highest polynomial degree integrated exactly (in exact arithmetic):
  // 2*size() - 1. Empty rule -> kStateConflict.
  Result<std::size_t> degree_of_exactness() const;

  // Affine image of this rule on [a, b].
  //   empty rule              -> kStateConflict
  //   a >= b or non-finite    -> kInvalidInput
  Result<GaussRule> map_to(double a, double b) const;

 private:
  friend class LegendreSolver;

  GaussRule(std::vector<double> nodes, std::vector<double> weights, double lower,
            double upper)
      : nodes_(std::move(nodes)),
        weights_(std::move(weights)),
        lower_(lower),
        upper_(upper) {}

  std::vector<double> nodes_;
  std::vector<double> weights_;
  double lower_ = std::numeric_limits<double>::quiet_NaN();
  double upper_ = std::numeric_limits<double>::quiet_NaN();
};

}  // namespace gauss
