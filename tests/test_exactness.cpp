// Exactness tests: polynomial moments up to the theoretical ceiling 2n-1
// must be integrated to within tolerance, and the report must identify the
// first failing degree. For x^2n the true error is well above tolerance for
// moderate n, so the first failure must be reported exactly at degree 2n.

#include <cstddef>
#include <vector>

#include "gauss/exactness.hpp"
#include "gauss/legendre.hpp"
#include "tests.hpp"

namespace gauss::test {

void test_polynomial_exactness(TestContext& ctx) {
  constexpr double kTol = 5e-11;
  const LegendreSolver solver;
  const std::vector<std::size_t> orders = {1, 2, 3, 4, 6, 8, 12, 16, 24, 32};
  for (const std::size_t n : orders) {
    auto rule = solver.compute(n);
    GAUSS_CHECK(ctx, rule.has_value(), "solver must succeed");
    if (!rule) continue;
    auto report = analyze_exactness(rule.value(), kTol);
    GAUSS_CHECK(ctx, report.has_value(), "exactness analysis must succeed");
    if (!report) continue;
    const ExactnessReport& r = report.value();

    const DegreeResidual& at_ceiling = r.degrees[2 * n - 1];
    const DegreeResidual& beyond = r.degrees[2 * n];
    ctx.state({{"order", static_cast<long long>(n)},
               {"verified_exact_degree",
                static_cast<long long>(r.verified_exact_degree)},
               {"residual_at_2n_minus_1", at_ceiling.rel_residual},
               {"residual_at_2n", beyond.rel_residual},
               {"max_rel_residual_within_theory",
                r.max_rel_residual_within_theory}});

    GAUSS_CHECK(ctx, r.theoretical_attained,
                "rule must be exact for all degrees up to 2n-1");
    GAUSS_CHECK(ctx, r.verified_exact_degree >= 2 * n - 1,
                "verified exactness degree must reach the theoretical ceiling");
    GAUSS_CHECK(ctx, r.max_rel_residual_within_theory <= kTol,
                "residuals within the theoretical range must stay below tolerance");
    if (n <= 16) {
      // For these orders the true error of x^2n is far above kTol, so the
      // first failing degree is exactly 2n. (For larger n the true error
      // underflows the tolerance; see ExactnessReport docs.)
      GAUSS_CHECK(ctx, r.first_failure_degree.has_value() &&
                           *r.first_failure_degree == 2 * n,
                  "first failing degree must be exactly 2n");
    }
  }
}

void test_exactness_on_mapped_interval(TestContext& ctx) {
  constexpr double kTol = 5e-11;
  const LegendreSolver solver;
  auto base = solver.compute(6);
  GAUSS_CHECK(ctx, base.has_value(), "solver must succeed");
  if (!base) return;
  auto mapped = base.value().map_to(0.0, 1.0);
  GAUSS_CHECK(ctx, mapped.has_value(), "mapping must succeed");
  if (!mapped) return;
  auto report = analyze_exactness(mapped.value(), kTol);
  GAUSS_CHECK(ctx, report.has_value(), "exactness analysis must succeed");
  if (!report) return;
  ctx.state({{"verified_exact_degree",
              static_cast<long long>(report.value().verified_exact_degree)},
             {"max_rel_residual_within_theory",
              report.value().max_rel_residual_within_theory}});
  GAUSS_CHECK(ctx, report.value().theoretical_attained,
              "a mapped rule keeps its exactness degree on [0,1]");
  GAUSS_CHECK(ctx, report.value().verified_exact_degree >= 11,
              "6-point rule must be exact through degree 11 on [0,1]");
}

}  // namespace gauss::test
