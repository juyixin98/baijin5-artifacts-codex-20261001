// Behavior tests: node symmetry, weight positivity, weight sum, interval
// mapping. These are the phase-2 acceptance behaviors of the kernel.

#include <cmath>
#include <vector>

#include "gauss/legendre.hpp"
#include "tests.hpp"

namespace gauss::test {

void test_symmetry_weights_positivity_sum(TestContext& ctx) {
  const std::vector<std::size_t> orders = {1, 2, 3, 4, 5, 7, 8, 16, 31, 32, 64, 100};
  const LegendreSolver solver;
  for (const std::size_t n : orders) {
    auto result = solver.compute(n);
    GAUSS_CHECK(ctx, result.has_value(), "solver must succeed for in-range order");
    if (!result) continue;
    const GaussRule& rule = result.value();
    const auto& x = rule.nodes();
    const auto& w = rule.weights();

    GAUSS_CHECK(ctx, x.size() == n && w.size() == n,
                "rule must contain exactly n nodes and n weights");

    bool nodes_bit_symmetric = true;
    bool weights_bit_symmetric = true;
    bool weights_positive = true;
    bool strictly_ascending = true;
    bool inside_interval = true;
    double weight_sum = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
      if (x[i] != -x[n - 1 - i]) nodes_bit_symmetric = false;
      if (w[i] != w[n - 1 - i]) weights_bit_symmetric = false;
      if (!(w[i] > 0.0)) weights_positive = false;
      if (i > 0 && !(x[i] > x[i - 1])) strictly_ascending = false;
      if (!(x[i] > -1.0 && x[i] < 1.0)) inside_interval = false;
      weight_sum += w[i];
    }
    ctx.state({{"order", static_cast<long long>(n)},
               {"weight_sum", weight_sum},
               {"first_node", x.front()},
               {"last_node", x.back()}});
    GAUSS_CHECK(ctx, nodes_bit_symmetric,
                "nodes must be exactly antisymmetric: x[i] == -x[n-1-i]");
    GAUSS_CHECK(ctx, weights_bit_symmetric,
                "weights must be exactly symmetric: w[i] == w[n-1-i]");
    GAUSS_CHECK(ctx, weights_positive, "every weight must be positive");
    GAUSS_CHECK(ctx, strictly_ascending, "nodes must be strictly ascending");
    GAUSS_CHECK(ctx, inside_interval, "nodes must lie inside (-1, 1)");
    GAUSS_CHECK_CLOSE(ctx, weight_sum, 2.0, 1e-12,
                      "weights on [-1,1] must sum to the interval length 2");
  }
}

void test_interval_mapping(TestContext& ctx) {
  const LegendreSolver solver;
  auto base = solver.compute(8);
  GAUSS_CHECK(ctx, base.has_value(), "base rule computation must succeed");
  if (!base) return;

  auto mapped = base.value().map_to(2.0, 5.0);
  GAUSS_CHECK(ctx, mapped.has_value(), "mapping onto a valid interval must succeed");
  if (!mapped) return;
  const GaussRule& rule = mapped.value();
  const auto& x = rule.nodes();
  const auto& w = rule.weights();

  double weight_sum = 0.0;
  bool inside = true;
  bool symmetric_about_midpoint = true;
  double first_moment = 0.0;
  for (std::size_t i = 0; i < rule.size(); ++i) {
    weight_sum += w[i];
    first_moment += w[i] * x[i];
    if (!(x[i] > 2.0 && x[i] < 5.0)) inside = false;
    // Pairwise symmetry of the mapped nodes about the interval midpoint.
    if (std::abs(x[i] + x[rule.size() - 1 - i] - 7.0) > 1e-13) {
      symmetric_about_midpoint = false;
    }
  }
  ctx.state({{"weight_sum", weight_sum}, {"first_moment", first_moment}});
  GAUSS_CHECK_CLOSE(ctx, weight_sum, 3.0, 1e-12,
                    "weights on [2,5] must sum to the interval length 3");
  GAUSS_CHECK(ctx, inside, "mapped nodes must lie inside (2, 5)");
  GAUSS_CHECK(ctx, symmetric_about_midpoint,
              "mapped nodes must be symmetric about the midpoint 3.5");
  GAUSS_CHECK_CLOSE(ctx, first_moment, 0.5 * (25.0 - 4.0), 1e-12,
                    "an 8-point rule integrates x on [2,5] exactly");
}

}  // namespace gauss::test
