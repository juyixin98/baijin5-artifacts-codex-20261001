#include "gauss/exactness.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>

#include <Eigen/Dense>

namespace gauss {

Result<ExactnessReport> analyze_exactness(const GaussRule& rule, double tolerance) {
  if (rule.empty()) {
    return Result<ExactnessReport>::fail(
        Error{ErrorCategory::kStateConflict,
              "analyze_exactness() on an empty rule; compute a rule first"});
  }
  if (!(tolerance > 0.0) || !std::isfinite(tolerance)) {
    return Result<ExactnessReport>::fail(
        Error{ErrorCategory::kInvalidInput, "tolerance must be positive and finite"}
            .with_diagnostic("tolerance", std::to_string(tolerance)));
  }

  const std::size_t n = rule.size();
  const double a = rule.interval_lower();
  const double b = rule.interval_upper();

  Eigen::Map<const Eigen::VectorXd> weights(rule.weights().data(),
                                            static_cast<Eigen::Index>(n));
  Eigen::Map<const Eigen::VectorXd> nodes(rule.nodes().data(),
                                          static_cast<Eigen::Index>(n));

  ExactnessReport report;
  report.order = n;
  report.theoretical_exact_degree = 2 * n - 1;
  report.tolerance = tolerance;

  // Degrees 0 .. 2n+2: the full theoretical range plus a look beyond the
  // ceiling so the first failing degree is visible in the report.
  const std::size_t max_degree = 2 * n + 2;
  report.degrees.reserve(max_degree + 1);

  std::size_t verified = 0;
  bool still_exact = true;
  for (std::size_t k = 0; k <= max_degree; ++k) {
    DegreeResidual entry;
    entry.degree = k;
    entry.exact_moment =
        (std::pow(b, static_cast<double>(k) + 1.0) -
         std::pow(a, static_cast<double>(k) + 1.0)) /
        (static_cast<double>(k) + 1.0);
    entry.quadrature_moment =
        weights.dot(nodes.array().pow(static_cast<double>(k)).matrix());
    entry.abs_residual = std::abs(entry.quadrature_moment - entry.exact_moment);
    entry.rel_residual =
        entry.abs_residual / std::max(1.0, std::abs(entry.exact_moment));
    entry.within_tolerance = entry.rel_residual <= tolerance;
    if (entry.within_tolerance && still_exact) {
      verified = k;
    } else if (!entry.within_tolerance) {
      if (still_exact) {
        report.first_failure_degree = k;
      }
      still_exact = false;
    }
    if (k <= report.theoretical_exact_degree) {
      report.max_rel_residual_within_theory =
          std::max(report.max_rel_residual_within_theory, entry.rel_residual);
    }
    report.degrees.push_back(entry);
  }
  report.verified_exact_degree = verified;
  report.theoretical_attained = verified >= report.theoretical_exact_degree;
  return Result<ExactnessReport>::ok(std::move(report));
}

std::string ExactnessReport::summary() const {
  std::ostringstream os;
  os << "Gauss-Legendre exactness report\n"
     << "  order n                       : " << order << "\n"
     << "  theoretical exactness degree  : " << theoretical_exact_degree
     << " (2n - 1)\n"
     << "  verified exactness degree     : " << verified_exact_degree
     << " (tolerance " << tolerance << ")\n"
     << "  theoretical ceiling attained  : "
     << (theoretical_attained ? "yes" : "no") << "\n"
     << "  max rel residual within theory: " << max_rel_residual_within_theory
     << "\n"
     << "  first failing degree          : ";
  if (first_failure_degree) {
    os << *first_failure_degree;
  } else {
    os << "none within [0, " << (degrees.empty() ? 0 : degrees.back().degree)
       << "]";
  }
  os << "\n  note: degrees above 2n - 1 may still pass when the true\n"
        "  quadrature error underflows the tolerance; that is an observed\n"
        "  property of this precision, not a guarantee.";
  return os.str();
}

}  // namespace gauss
