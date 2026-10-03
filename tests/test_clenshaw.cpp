#include "test_framework.hpp"

#include <cmath>
#include <random>

#include "cheb/evaluate.hpp"
#include "cheb/expansion.hpp"

using namespace cheb;

TEST_CASE("clenshaw.t4_matches_function_at_endpoints_and_interior") {
  auto ex = cheb_fit([](double t) { return t * t * t * t; },
                     Interval{-1, 1}, 4);
  // Endpoints are the classic silent-failure points: exact values required.
  CHECK_NEAR(clenshaw(ex, -1.0), 1.0, 1e-15, "clenshaw_endpoint_minus1");
  CHECK_NEAR(clenshaw(ex, 1.0), 1.0, 1e-15, "clenshaw_endpoint_plus1");
  CHECK_NEAR(clenshaw(ex, 0.0), 0.0, 1e-15, "clenshaw_midpoint");
  std::mt19937 rng(1234);
  std::uniform_real_distribution<double> u(-1.0, 1.0);
  for (int i = 0; i < 200; ++i) {
    const double t = u(rng);
    CHECK_NEAR(clenshaw(ex, t), t * t * t * t, 1e-14,
               "clenshaw_random_interior");
  }
}

TEST_CASE("clenshaw.mapped_x2_endpoints_and_vectorised") {
  auto ex = cheb_fit([](double x) { return x * x; }, Interval{2.0, 4.0}, 2);
  CHECK_NEAR(clenshaw(ex, 2.0), 4.0, 1e-13, "mapped_eval_lo");
  CHECK_NEAR(clenshaw(ex, 4.0), 16.0, 1e-13, "mapped_eval_hi");
  CHECK_NEAR(clenshaw(ex, 3.0), 9.0, 1e-13, "mapped_eval_mid");
  Eigen::VectorXd xs(3), want(3);
  xs << 2.0, 3.0, 4.0;
  want << 4.0, 9.0, 16.0;
  const auto got = clenshaw(ex, xs);
  for (int i = 0; i < 3; ++i)
    CHECK_NEAR(got[i], want[i], 1e-13, "mapped_vector_eval");
}

TEST_CASE("clenshaw.abs_interpolation_exact_at_nodes_only") {
  const int n = 64;
  auto ex = cheb_fit([](double t) { return std::fabs(t); },
                     Interval{-1, 1}, n);
  // Interpolation identity at every closed node (incl. the cusp t=0 which is a
  // node iff n even).
  const auto nodes = reference_nodes(n);
  for (int j = 0; j <= n; ++j) {
    CHECK_NEAR(clenshaw_reference(ex.coeff, nodes[j]),
               std::fabs(nodes[j]), 1e-12, "clenshaw_node_interpolation");
  }
  // Off-node error near the cusp is nonzero: interpolation is not magic.
  const double e_cusp =
      std::fabs(clenshaw_reference(ex.coeff, 1.0 / (2.0 * n)) -
                1.0 / (2.0 * n));
  CHECK_TRUE(e_cusp > 1e-6, "nonsmooth_offnode_error_present");
}

TEST_CASE("clenshaw.linear_polynomial") {
  // 3 - 2t
  ChebExpansion ex;
  ex.interval = Interval{-1, 1};
  ex.coeff = Eigen::VectorXd{{3.0, -2.0}};
  for (double t : {-1.0, 0.0, 0.7, 1.0})
    CHECK_NEAR(clenshaw_reference(ex.coeff, t), 3 - 2 * t, 1e-15,
               "clenshaw_linear");
}

int main() { return chebtest::run_all(); }
