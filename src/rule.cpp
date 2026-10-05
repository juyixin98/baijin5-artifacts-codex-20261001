#include "gauss/rule.hpp"

#include <cmath>
#include <sstream>

namespace gauss {

namespace {

std::string num(double v) {
  std::ostringstream os;
  os.precision(17);
  os << v;
  return os.str();
}

}  // namespace

double default_convergence_tolerance() noexcept {
  // A few ulps: tight enough that a converged root is accurate to machine
  // precision, loose enough that Newton's quadratic convergence reaches it
  // in a handful of iterations.
  return 8.0 * std::numeric_limits<double>::epsilon();
}

Result<std::size_t> GaussRule::degree_of_exactness() const {
  if (empty()) {
    return Result<std::size_t>::fail(
        Error{ErrorCategory::kStateConflict,
              "degree_of_exactness() on an empty rule; compute a rule first"});
  }
  return Result<std::size_t>::ok(2 * size() - 1);
}

Result<GaussRule> GaussRule::map_to(double a, double b) const {
  if (empty()) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kStateConflict,
              "map_to() on an empty rule; compute a rule first"});
  }
  if (!std::isfinite(a) || !std::isfinite(b)) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kInvalidInput, "interval bounds must be finite"}
            .with_diagnostic("a", num(a))
            .with_diagnostic("b", num(b)));
  }
  if (!(a < b)) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kInvalidInput, "interval must satisfy a < b"}
            .with_diagnostic("a", num(a))
            .with_diagnostic("b", num(b)));
  }
  const double mid = 0.5 * (a + b);
  const double half = 0.5 * (b - a);
  std::vector<double> nodes(nodes_.size());
  std::vector<double> weights(weights_.size());
  for (std::size_t i = 0; i < nodes_.size(); ++i) {
    nodes[i] = mid + half * nodes_[i];
    weights[i] = half * weights_[i];
  }
  return Result<GaussRule>::ok(
      GaussRule(std::move(nodes), std::move(weights), a, b));
}

}  // namespace gauss
