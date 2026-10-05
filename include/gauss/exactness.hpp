#pragma once

// Error-interpretation module: verifies the polynomial exactness of a rule
// against analytically known moments and reports residuals per degree.
//
// The report distinguishes two "precision ceilings":
//   * theoretical_exact_degree = 2n - 1 (exact arithmetic)
//   * verified_exact_degree    = highest degree whose quadrature moment
//     matches the analytic moment within the requested tolerance in
//     floating point
// For degrees far beyond 2n - 1 the true quadrature error can underflow the
// tolerance (e.g. for x^2n when n is large), so an observed pass above the
// theoretical ceiling is reported as-is, not treated as a guarantee.

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

#include "gauss/error.hpp"
#include "gauss/rule.hpp"

namespace gauss {

struct DegreeResidual {
  std::size_t degree = 0;
  double exact_moment = 0.0;       // integral of x^degree over the rule's interval
  double quadrature_moment = 0.0;  // sum_i w_i * x_i^degree
  double abs_residual = 0.0;       // |quadrature - exact|
  // abs_residual / max(|exact|, 1): a mixed absolute/relative measure that
  // stays meaningful for the vanishing odd moments on symmetric intervals.
  double rel_residual = 0.0;
  bool within_tolerance = false;
};

struct ExactnessReport {
  std::size_t order = 0;
  std::size_t theoretical_exact_degree = 0;
  std::size_t verified_exact_degree = 0;  // consecutive from degree 0
  bool theoretical_attained = false;
  std::optional<std::size_t> first_failure_degree;  // nullopt: nothing failed
  double max_rel_residual_within_theory = 0.0;
  double tolerance = 0.0;
  std::vector<DegreeResidual> degrees;  // one entry per degree 0 .. 2n+2

  std::string summary() const;
};

//   empty rule                       -> kStateConflict
//   tolerance <= 0 or non-finite     -> kInvalidInput
Result<ExactnessReport> analyze_exactness(const GaussRule& rule, double tolerance);

}  // namespace gauss
