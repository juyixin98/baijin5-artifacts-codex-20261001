#include "gauss/legendre.hpp"

#include <cmath>
#include <new>
#include <sstream>

namespace gauss {

namespace {

constexpr double kPi = 3.14159265358979323846264338327950288;

std::string num(double v) {
  std::ostringstream os;
  os.precision(17);
  os << v;
  return os.str();
}

// Legendre polynomial P_n(x) and its derivative via the three-term
// recurrence. Requires n >= 1 and |x| < 1.
std::pair<double, double> legendre_and_derivative(std::size_t n, double x) {
  double p_prev = 1.0;  // P_0
  double p_cur = x;     // P_1
  for (std::size_t k = 1; k < n; ++k) {
    const double p_next =
        ((2.0 * static_cast<double>(k) + 1.0) * x * p_cur -
         static_cast<double>(k) * p_prev) /
        (static_cast<double>(k) + 1.0);
    p_prev = p_cur;
    p_cur = p_next;
  }
  const double derivative =
      static_cast<double>(n) * (x * p_cur - p_prev) / (x * x - 1.0);
  return {p_cur, derivative};
}

Error invalid_options(std::string message) {
  return Error{ErrorCategory::kInvalidInput, std::move(message)};
}

}  // namespace

Result<GaussRule> LegendreSolver::compute(std::size_t order) const {
  if (order == 0) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kInvalidInput, "order must be >= 1"}
            .with_diagnostic("order", "0"));
  }
  if (options_.max_order == 0) {
    return Result<GaussRule>::fail(
        invalid_options("SolverOptions::max_order must be >= 1"));
  }
  if (options_.max_iterations == 0) {
    return Result<GaussRule>::fail(
        invalid_options("SolverOptions::max_iterations must be >= 1"));
  }
  if (options_.convergence_tolerance < 0.0 ||
      !std::isfinite(options_.convergence_tolerance)) {
    return Result<GaussRule>::fail(
        invalid_options("SolverOptions::convergence_tolerance must be >= 0 and finite")
            .with_diagnostic("convergence_tolerance",
                             num(options_.convergence_tolerance)));
  }
  if (order > options_.max_order) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kResourceExhaustion,
              "order exceeds the configured max_order limit"}
            .with_diagnostic("order", std::to_string(order))
            .with_diagnostic("max_order", std::to_string(options_.max_order)));
  }

  const double tol = options_.convergence_tolerance > 0.0
                         ? options_.convergence_tolerance
                         : default_convergence_tolerance();

  try {
    std::vector<double> nodes(order);
    std::vector<double> weights(order);

    // Only the roots in (0, 1] are iterated; the rest follow by symmetry.
    const std::size_t half = (order + 1) / 2;
    for (std::size_t i = 0; i < half; ++i) {
      // Tricomi initial guess for the (i+1)-th largest root.
      double x = std::cos(kPi * (static_cast<double>(i) + 0.75) /
                          (static_cast<double>(order) + 0.5));
      double step = 0.0;
      double residual = 0.0;
      bool converged = false;
      std::size_t iteration = 0;
      for (; iteration < options_.max_iterations; ++iteration) {
        const auto [p, dp] = legendre_and_derivative(order, x);
        residual = std::abs(p);
        step = -p / dp;
        x += step;
        if (!std::isfinite(x)) break;
        if (std::abs(step) <= tol * std::max(1.0, std::abs(x))) {
          converged = true;
          break;
        }
      }
      if (!converged || !std::isfinite(x) || std::abs(x) >= 1.0) {
        return Result<GaussRule>::fail(
            Error{ErrorCategory::kComputationFailure,
                  "Newton iteration for a Legendre root did not converge; "
                  "refusing to return an unconverged node"}
                .with_diagnostic("root_index_from_edge", std::to_string(i))
                .with_diagnostic("iterations", std::to_string(iteration))
                .with_diagnostic("max_iterations",
                                 std::to_string(options_.max_iterations))
                .with_diagnostic("last_step", num(std::abs(step)))
                .with_diagnostic("residual", num(residual))
                .with_diagnostic("tolerance", num(tol)));
      }
      // For odd orders the middle root of P_n is exactly 0; snap it so the
      // mirrored rule stays antisymmetric bit-for-bit.
      if (order % 2 == 1 && i == order / 2) {
        x = 0.0;
      }
      const auto [p_final, dp_final] = legendre_and_derivative(order, x);
      (void)p_final;
      const double w = 2.0 / ((1.0 - x * x) * dp_final * dp_final);
      if (!(w > 0.0) || !std::isfinite(w)) {
        return Result<GaussRule>::fail(
            Error{ErrorCategory::kComputationFailure,
                  "converged root produced a non-positive or non-finite weight"}
                .with_diagnostic("root_index_from_edge", std::to_string(i))
                .with_diagnostic("node", num(x))
                .with_diagnostic("weight", num(w)));
      }
      nodes[i] = -x;
      nodes[order - 1 - i] = x;
      weights[i] = w;
      weights[order - 1 - i] = w;
    }

    return Result<GaussRule>::ok(
        GaussRule(std::move(nodes), std::move(weights), -1.0, 1.0));
  } catch (const std::bad_alloc&) {
    return Result<GaussRule>::fail(
        Error{ErrorCategory::kResourceExhaustion,
              "memory allocation failed while building the rule"}
            .with_diagnostic("order", std::to_string(order)));
  }
}

}  // namespace gauss
