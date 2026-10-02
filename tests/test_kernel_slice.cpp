#include <cmath>
#include <vector>

#include "chebcore/kernel.hpp"
#include "test_framework.hpp"

using namespace chebcore;

// Earliest real executable test: known closed-form answers.
TEST_CASE("slice:x^2 on [-1,1] has exact coefficients [+1/2,0,+1/2]") {
  auto fit = fit_sample([](double x) { return x * x; }, Interval{-1, 1}, 2);
  REQUIRE_EQ(fit.coeffs.size(), std::size_t(3));
  REQUIRE_ABS(fit.coeffs[0], +0.5, 1e-15);
  REQUIRE_ABS(fit.coeffs[1], 0.0, 1e-15);
  REQUIRE_ABS(fit.coeffs[2], 0.5, 1e-15);
}

TEST_CASE("slice:nodes include exact endpoints t=+/-1") {
  auto t = lobatto_nodes(4);
  REQUIRE_ABS(t.front(), 1.0, 0.0);
  REQUIRE_ABS(t.back(), -1.0, 0.0);
}

TEST_CASE("slice:Clenshaw reproduces samples at all nodes") {
  auto fit = fit_sample([](double x) { return std::sin(3.0 * x); },
                        Interval{-1.2, 2.5}, 8);
  for (std::size_t j = 0; j <= fit.degree; ++j) {
    REQUIRE_ABS(fit.eval_physical(sample_nodes(fit.domain, 8)[j]),
                fit.sample_values[j], 1e-12);
  }
}

TEST_CASE("slice:Clenshaw and direct cosine sum agree incl endpoints") {
  auto fit = fit_sample([](double x) { return std::exp(x) - 0.3 * x * x; },
                        Interval{-1, 1}, 6);
  for (double t : {-1.0, -0.9, 0.0, 0.73, 1.0}) {
    REQUIRE_ABS(clenshaw(fit.coeffs, t), direct_cos_sum(fit.coeffs, t), 1e-12);
  }
}

int main() { return chebtest::run_all(); }
