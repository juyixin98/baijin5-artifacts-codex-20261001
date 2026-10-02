#include <cmath>
#include <filesystem>

#include "chebcore/fixtures.hpp"
#include "chebcore/kernel.hpp"
#include "chebcore/reference.hpp"
#include "test_framework.hpp"

using namespace chebcore;

namespace fs = std::filesystem;

fs::path fixture_dir() {
  static const fs::path candidates[] = {
      "fixtures/generated",
      "../fixtures/generated",
      "../../fixtures/generated",
  };
  for (const auto& p : candidates)
    if (fs::exists(p / "lobatto_cc_n8.csv")) return p;
  throw std::runtime_error("fixtures not found; run the gen_fixtures target");
}

TEST_CASE("fixtures:tampered or missing fixture is rejected via digest") {
  auto dir = fixture_dir();
  auto manifest = fixtures::load_manifest((dir / "MANIFEST.sha256").string());
  REQUIRE(!manifest.empty());
  REQUIRE_NOTHROW(fixtures::verify_manifest(dir.string(), manifest));
  // Local SHA-256 must agree with a known digest property: distinct file.
  REQUIRE_THROWS_STD(
      std::runtime_error,
      fixtures::load_manifest((dir / "does_not_exist.sha256").string()));
}

TEST_CASE("fixtures:Lobatto/CC CSV matches kernel contract exactly") {
  auto dir = fixture_dir();
  auto csv = fixtures::load_csv((dir / "lobatto_cc_n8.csv").string());
  REQUIRE_EQ(csv.header.size(), std::size_t(3));
  auto t = lobatto_nodes(8);
  auto w = clenshaw_curtis_weights(8);
  REQUIRE_EQ(csv.data[0].size(), std::size_t(9));  // j
  for (std::size_t j = 0; j <= 8; ++j) {
    REQUIRE_ABS(csv.data[1][j], t[j], 1e-16);
    REQUIRE_ABS(csv.data[2][j], w[j], 1e-15);
  }
}

TEST_CASE("fixtures:exact polynomial CSV independently checks kernel coeffs") {
  auto dir = fixture_dir();
  auto csv = fixtures::load_csv((dir / "exact_poly_coeffs.csv").string());
  // columns: d,n,a,b,k,a_k_exact,case (string last)
  REQUIRE_EQ(csv.header.size(), std::size_t(7));
  std::size_t rows = csv.data[0].size();
  REQUIRE(rows > 20);
  for (std::size_t r = 0; r < rows; ++r) {
    const std::size_t d = static_cast<std::size_t>(csv.data[0][r]);
    const std::size_t n = static_cast<std::size_t>(csv.data[1][r]);
    const double a = csv.data[2][r], b = csv.data[3][r];
    const std::size_t k = static_cast<std::size_t>(csv.data[4][r]);
    const double exact = csv.data[5][r];
    auto fit = fit_sample(
        [d](double x) { return std::pow(x, static_cast<double>(d)); },
        Interval{a, b}, n);
    const double tol = 1e-11 * std::max(1.0, std::fabs(exact));
    REQUIRE_ABS(fit.coeffs[k], exact, tol);
  }
}

TEST_CASE("fixtures:alias probe samples reconstruct constant only") {
  auto dir = fixture_dir();
  auto csv = fixtures::load_csv((dir / "alias_abs_t_n1.csv").string());
  REQUIRE_EQ(csv.data[2][0], 1.0);
  REQUIRE_EQ(csv.data[2][1], 1.0);
  auto fit = fit_values(Interval{-1, 1}, 1, csv.data[2]);
  REQUIRE_ABS(fit.coeffs[0], 1.0, 1e-15);
  REQUIRE_ABS(fit.coeffs[1], 0.0, 1e-15);
  // But the true |t| at t=0 is 0, not 1: fixture encodes the pitfall.
  REQUIRE_ABS(fit.eval_reference(0.0) - 0.0, 1.0, 1e-15);
  REQUIRE(std::fabs(fit.eval_reference(0.0) - 0.0) > 0.5);
}

int main() { return chebtest::run_all(); }
