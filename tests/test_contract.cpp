#include <cmath>
#include <stdexcept>

#include "chebcore/numeric_contract.hpp"
#include "test_framework.hpp"

using namespace chebcore;

TEST_CASE("contract:affine map is exact at endpoints and center") {
  const Interval d{2.0, 4.0};
  REQUIRE_ABS(to_reference(2.0, d), -1.0, 0.0);
  REQUIRE_ABS(to_reference(4.0, d), 1.0, 0.0);
  REQUIRE_ABS(to_reference(3.0, d), 0.0, 0.0);
  REQUIRE_ABS(from_reference(-1.0, d), 2.0, 0.0);
  REQUIRE_ABS(from_reference(1.0, d), 4.0, 0.0);
  for (double x : {2.1, 2.7, 3.33, 3.99})
    REQUIRE_ABS(from_reference(to_reference(x, d), d), x, 1e-14);
}

TEST_CASE("contract:reversed interval is rejected") {
  REQUIRE_THROWS_STD(std::invalid_argument,
                     to_reference(0.0, Interval{1.0, 1.0}));
  REQUIRE_THROWS_STD(std::invalid_argument,
                     to_reference(0.0, Interval{2.0, -2.0}));
  REQUIRE_THROWS_STD(std::invalid_argument,
                     to_reference(0.0,
                                  Interval{std::nan(""), 1.0}));
  REQUIRE((!Interval{1, 1}.valid()));
  REQUIRE((!Interval{0, std::numeric_limits<double>::infinity()}.valid()));
}

TEST_CASE("contract:degree 0 grid rejected (cannot carry both endpoints)") {
  REQUIRE_THROWS_STD(std::invalid_argument, lobatto_nodes(0));
  REQUIRE_THROWS_STD(std::invalid_argument, clenshaw_curtis_weights(0));
}

TEST_CASE("contract:Lobatto nodes descending with exact +/-1 endpoints") {
  for (std::size_t n : {1u, 2u, 3u, 8u, 17u, 32u}) {
    auto t = lobatto_nodes(n);
    REQUIRE_EQ(t.size(), n + 1);
    REQUIRE_ABS(t.front(), 1.0, 0.0);
    REQUIRE_ABS(t.back(), -1.0, 0.0);
    for (std::size_t j = 1; j < t.size(); ++j)
      if (!(t[j] < t[j - 1]))
        chebtest::fail("ORDERING", "nodes not strictly descending at n=" +
                                       std::to_string(n));
  }
}

TEST_CASE("contract:CC weights match closed-form n=1 and n=2") {
  auto w1 = clenshaw_curtis_weights(1);
  REQUIRE_ABS(w1[0], 1.0, 1e-15);
  REQUIRE_ABS(w1[1], 1.0, 1e-15);
  auto w2 = clenshaw_curtis_weights(2);
  REQUIRE_ABS(w2[0], 1.0 / 3.0, 1e-15);
  REQUIRE_ABS(w2[1], 4.0 / 3.0, 1e-15);
  REQUIRE_ABS(w2[2], 1.0 / 3.0, 1e-15);
}

TEST_CASE("contract:CC integrates every monomial degree<=n exactly") {
  for (std::size_t n = 1; n <= 12; ++n) {
    auto t = lobatto_nodes(n);
    auto w = clenshaw_curtis_weights(n);
    for (std::size_t k = 0; k <= n; ++k) {
      double s = 0.0;
      for (std::size_t j = 0; j <= n; ++j)
        s += w[j] * std::pow(t[j], static_cast<double>(k));
      const double exact = (k % 2) ? 0.0 : 2.0 / static_cast<double>(k + 1);
      REQUIRE_ABS(s, exact, 1e-10);
    }
  }
}

TEST_CASE("contract:endpoint half weights give correct symmetry") {
  for (std::size_t n : {2u, 4u, 8u, 16u}) {
    auto w = clenshaw_curtis_weights(n);
    REQUIRE_ABS(w.front(), w.back(), 1e-15);  // symmetric endpoints
    double total = 0;
    for (double x : w) total += x;
    REQUIRE_ABS(total, 2.0, 1e-12);           // integrates constant 1 on [-1,1]
  }
}

int main() { return chebtest::run_all(); }
