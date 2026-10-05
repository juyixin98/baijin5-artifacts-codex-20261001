// Error-contract tests: each failure scenario must surface as exactly one
// documented ErrorCategory, and a failed computation must never leak
// partial results.

#include <cmath>
#include <limits>
#include <string>

#include "gauss/exactness.hpp"
#include "gauss/legendre.hpp"
#include "tests.hpp"

namespace gauss::test {

namespace {

bool has_diagnostic(const Error& error, const std::string& key) {
  for (const auto& [k, v] : error.diagnostics) {
    if (k == key) return true;
  }
  return false;
}

}  // namespace

void test_invalid_input(TestContext& ctx) {
  const LegendreSolver solver;

  GAUSS_CHECK_ERROR(ctx, solver.compute(0), ErrorCategory::kInvalidInput,
                    "order 0 is outside the documented domain");

  SolverOptions bad_tolerance;
  bad_tolerance.convergence_tolerance = -1e-3;
  GAUSS_CHECK_ERROR(ctx, LegendreSolver(bad_tolerance).compute(4),
                    ErrorCategory::kInvalidInput,
                    "negative convergence tolerance must be rejected");

  SolverOptions no_iterations;
  no_iterations.max_iterations = 0;
  GAUSS_CHECK_ERROR(ctx, LegendreSolver(no_iterations).compute(4),
                    ErrorCategory::kInvalidInput,
                    "max_iterations == 0 must be rejected");

  auto rule = solver.compute(4);
  GAUSS_CHECK(ctx, rule.has_value(), "valid order must succeed");
  if (!rule) return;
  GAUSS_CHECK_ERROR(ctx, rule.value().map_to(1.0, 1.0),
                    ErrorCategory::kInvalidInput, "empty interval must be rejected");
  GAUSS_CHECK_ERROR(ctx, rule.value().map_to(2.0, -3.0),
                    ErrorCategory::kInvalidInput, "reversed interval must be rejected");
  GAUSS_CHECK_ERROR(ctx,
                    rule.value().map_to(std::numeric_limits<double>::quiet_NaN(), 1.0),
                    ErrorCategory::kInvalidInput, "NaN bound must be rejected");
  GAUSS_CHECK_ERROR(ctx, analyze_exactness(rule.value(), 0.0),
                    ErrorCategory::kInvalidInput,
                    "zero tolerance must be rejected");
  GAUSS_CHECK_ERROR(ctx,
                    analyze_exactness(rule.value(),
                                      std::numeric_limits<double>::infinity()),
                    ErrorCategory::kInvalidInput,
                    "infinite tolerance must be rejected");
}

void test_resource_exhaustion(TestContext& ctx) {
  SolverOptions options;
  options.max_order = 8;
  const LegendreSolver solver(options);
  auto result = solver.compute(64);
  GAUSS_CHECK_ERROR(ctx, result, ErrorCategory::kResourceExhaustion,
                    "order above max_order must be a resource error");
  if (!result.has_value()) {
    GAUSS_CHECK(ctx, has_diagnostic(result.error(), "max_order"),
                "resource error must record the configured limit");
    GAUSS_CHECK(ctx, has_diagnostic(result.error(), "order"),
                "resource error must record the requested order");
  }
  // The same solver must still serve in-range requests: the failure is
  // per-request, not a poisoned state.
  GAUSS_CHECK(ctx, solver.compute(8).has_value(),
              "in-range order must still succeed after a rejected request");
}

void test_non_convergence_rejected(TestContext& ctx) {
  // One Newton step is never enough to reach 8*eps from the Tricomi guess,
  // so this forces the kernel's computation-failure path.
  SolverOptions starved;
  starved.max_iterations = 1;
  auto result = LegendreSolver(starved).compute(50);
  GAUSS_CHECK_ERROR(ctx, result, ErrorCategory::kComputationFailure,
                    "an unconverged root must be a computation failure");
  GAUSS_CHECK(ctx, !result.has_value(),
              "no rule (and therefore no unconverged node) may be returned");
  if (!result.has_value()) {
    const Error& e = result.error();
    ctx.state({{"error_message", e.message}});
    GAUSS_CHECK(ctx, has_diagnostic(e, "root_index_from_edge"),
                "failure must identify which root failed");
    GAUSS_CHECK(ctx, has_diagnostic(e, "iterations"),
                "failure must record the iteration count");
    GAUSS_CHECK(ctx, has_diagnostic(e, "residual"),
                "failure must record the final residual");
    GAUSS_CHECK(ctx, has_diagnostic(e, "last_step"),
                "failure must record the last Newton step");
  }

  SolverOptions two_steps;
  two_steps.max_iterations = 2;
  GAUSS_CHECK_ERROR(ctx, LegendreSolver(two_steps).compute(50),
                    ErrorCategory::kComputationFailure,
                    "two iterations are still not enough at order 50");

  // Contrast: the default budget converges comfortably.
  GAUSS_CHECK(ctx, LegendreSolver().compute(50).has_value(),
              "the same order must succeed with the default iteration budget");
}

void test_state_conflict(TestContext& ctx) {
  const GaussRule empty;  // default-constructed: no computation has happened
  GAUSS_CHECK(ctx, empty.empty(), "default rule must be empty");
  GAUSS_CHECK_ERROR(ctx, empty.map_to(0.0, 1.0), ErrorCategory::kStateConflict,
                    "mapping an empty rule is a state conflict");
  GAUSS_CHECK_ERROR(ctx, empty.degree_of_exactness(),
                    ErrorCategory::kStateConflict,
                    "exactness degree of an empty rule is a state conflict");
  GAUSS_CHECK_ERROR(ctx, analyze_exactness(empty, 1e-12),
                    ErrorCategory::kStateConflict,
                    "analyzing an empty rule is a state conflict");
}

}  // namespace gauss::test
