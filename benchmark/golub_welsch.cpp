#include "benchmark/golub_welsch.hpp"

#include <chrono>
#include <cmath>
#include <new>

#include <Eigen/Dense>
#include <Eigen/Eigenvalues>

#include "gauss/legendre.hpp"

namespace gauss::benchmark {

Result<RawRule> golub_welsch_legendre(std::size_t order) {
  if (order == 0) {
    return Result<RawRule>::fail(
        Error{ErrorCategory::kInvalidInput, "order must be >= 1"}
            .with_diagnostic("order", "0"));
  }
  try {
    const Eigen::Index n = static_cast<Eigen::Index>(order);
    Eigen::MatrixXd jacobi = Eigen::MatrixXd::Zero(n, n);
    for (Eigen::Index k = 1; k < n; ++k) {
      const double kd = static_cast<double>(k);
      const double beta = kd / std::sqrt(4.0 * kd * kd - 1.0);
      jacobi(k - 1, k) = beta;
      jacobi(k, k - 1) = beta;
    }
    const Eigen::SelfAdjointEigenSolver<Eigen::MatrixXd> solver(jacobi);
    if (solver.info() != Eigen::Success) {
      return Result<RawRule>::fail(
          Error{ErrorCategory::kComputationFailure,
                "Eigen SelfAdjointEigenSolver failed on the Jacobi matrix"}
              .with_diagnostic("order", std::to_string(order))
              .with_diagnostic("eigen_info",
                               std::to_string(static_cast<int>(solver.info()))));
    }
    RawRule rule;
    rule.nodes.resize(order);
    rule.weights.resize(order);
    for (Eigen::Index i = 0; i < n; ++i) {
      const std::size_t u = static_cast<std::size_t>(i);
      rule.nodes[u] = solver.eigenvalues()(i);
      const double v0 = solver.eigenvectors()(0, i);
      rule.weights[u] = 2.0 * v0 * v0;
    }
    return Result<RawRule>::ok(std::move(rule));
  } catch (const std::bad_alloc&) {
    return Result<RawRule>::fail(
        Error{ErrorCategory::kResourceExhaustion,
              "memory allocation failed in the Golub-Welsch reference"}
            .with_diagnostic("order", std::to_string(order)));
  }
}

CrossCheck cross_check_legendre(std::size_t order) {
  using clock = std::chrono::steady_clock;
  CrossCheck out;
  out.order = order;
  // Tolerances cover the eigensolver's own rounding at these orders while
  // remaining far tighter than any plausible algorithmic defect.
  out.node_tolerance = 2e-12;
  out.weight_tolerance = 5e-11;

  const auto t0 = clock::now();
  auto kernel = LegendreSolver().compute(order);
  const auto t1 = clock::now();
  auto reference = golub_welsch_legendre(order);
  const auto t2 = clock::now();
  out.kernel_seconds = std::chrono::duration<double>(t1 - t0).count();
  out.golub_welsch_seconds = std::chrono::duration<double>(t2 - t1).count();

  if (!kernel || !reference) {
    out.passed = false;
    return out;
  }
  const auto& x = kernel.value().nodes();
  const auto& w = kernel.value().weights();
  const auto& rx = reference.value().nodes;
  const auto& rw = reference.value().weights;
  for (std::size_t i = 0; i < order; ++i) {
    out.max_node_abs_diff =
        std::max(out.max_node_abs_diff, std::abs(x[i] - rx[i]));
    out.max_weight_abs_diff =
        std::max(out.max_weight_abs_diff, std::abs(w[i] - rw[i]));
  }
  out.passed = out.max_node_abs_diff <= out.node_tolerance &&
               out.max_weight_abs_diff <= out.weight_tolerance;
  return out;
}

}  // namespace gauss::benchmark
