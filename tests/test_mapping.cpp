#include "test_framework.hpp"

#include <cmath>

#include "cheb/interval.hpp"

using namespace cheb;

TEST_CASE("mapping.roundtrip_on_non_unit_interval") {
  const Interval iv{2.0, 4.0};
  for (double t : {-1.0, -0.5, 0.0, 0.25, 1.0}) {
    CHECK_NEAR(map_to_reference(iv, map_from_reference(iv, t)), t, 1e-15,
               "mapping_roundtrip");
  }
  CHECK_NEAR(map_from_reference(iv, -1.0), 2.0, 0.0, "mapping_endpoint_lo");
  CHECK_NEAR(map_from_reference(iv, 1.0), 4.0, 0.0, "mapping_endpoint_hi");
  CHECK_NEAR(map_to_reference(iv, 2.0), -1.0, 0.0, "mapping_endpoint_tlo");
  CHECK_NEAR(map_to_reference(iv, 4.0), 1.0, 0.0, "mapping_endpoint_thi");
}

TEST_CASE("mapping.closed_nodes_include_both_physical_endpoints") {
  const Interval iv{-3.0, 7.0};
  const int n = 8;
  const auto nodes = chebyshev_nodes(iv, n);
  CHECK_EQ(nodes.size(), n + 1, "node_count");
  CHECK_NEAR(nodes[0], 7.0, 0.0, "endpoint_hi_sampled");
  CHECK_NEAR(nodes[n], -3.0, 0.0, "endpoint_lo_sampled");
  // Strictly decreasing: t_j = cos(pi j/n).
  for (int j = 1; j <= n; ++j)
    CHECK_TRUE(nodes[j] < nodes[j - 1], "nodes_descending");
}

TEST_CASE("mapping.degenerate_and_bad_intervals_rejected") {
  CHECK_THROWS_STD(check_interval(Interval{1.0, 1.0}), "degenerate_interval");
  CHECK_THROWS_STD(check_interval(Interval{2.0, 1.0}), "reversed_interval");
  CHECK_THROWS_STD(check_interval(Interval{std::nan(""), 1.0}),
                   "nan_endpoint");
  CHECK_THROWS_STD(check_interval(Interval{1.0,
                                    std::numeric_limits<double>::infinity()}),
                   "infinite_endpoint");
  CHECK_THROWS_STD(reference_nodes(0), "degree_too_small");
}

int main() { return chebtest::run_all(); }
