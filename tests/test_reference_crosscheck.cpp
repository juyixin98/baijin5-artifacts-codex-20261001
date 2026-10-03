#include "test_framework.hpp"

#include <cmath>
#include <fstream>
#include <sstream>
#include <string>

#include "cheb/expansion.hpp"
#include "reference/reference.hpp"

using namespace cheb;

// Core (double, DCT + Clenshaw) vs independent long-double reference
// (Kahan cosine sum + explicit basis).
static void crosscheck(const std::string& name, const chebref::FuncLD& fld,
                       const cheb::Func& fd, double lo, double hi, int n,
                       double coeff_tol, double residual_tol) {
  if (!chebref::precision_guard()) {
    std::printf("[ SKIP ] %s (no extended long double)\n", name.c_str());
    return;
  }
  const auto a = chebref::fit(fld, lo, hi, n);
  auto ex = cheb::cheb_fit(fd, cheb::Interval{lo, hi}, n);
  for (int k = 0; k <= n; ++k)
    CHECK_NEAR(ex.coeff[k], (double)a[k], coeff_tol,
               "core_vs_ref_coeff_" + name);
  const long double mr = chebref::max_residual(a, lo, hi, fld, 3000);
  // Independent residual measured in long double must be tiny for a smooth fn.
  CHECK_TRUE((double)mr < residual_tol,
             "ref_residual_" + name + " mr=" +
                 std::to_string((double)mr));
}

TEST_CASE("ref.t4_core_agrees_with_independent_longdouble") {
  crosscheck("t4", [](long double x) { return x * x * x * x; },
             [](double x) { return x * x * x * x; }, -1, 1, 4, 1e-15, 1e-15);
}

TEST_CASE("ref.exp_core_agrees_with_independent_longdouble") {
  crosscheck("exp", [](long double x) { return std::exp(x); },
             [](double x) { return std::exp(x); }, -1, 1, 40, 1e-12, 1e-14);
}

TEST_CASE("ref.mapped_x2_core_agrees_with_independent_longdouble") {
  crosscheck("x2_mapped", [](long double x) { return x * x; },
             [](double x) { return x * x; }, 2, 4, 2, 1e-12, 1e-14);
}

TEST_CASE("ref.hardcoded_fixture_files_match_core_and_reference") {
  // The committed fixture is hand-authored; load it and compare BOTH the
  // double core and the long double reference (series convention -> halve cn).
  if (!chebref::precision_guard()) {
    std::printf("[ SKIP ] fixture (no extended long double)\n");
    return;
  }
  std::ifstream in("tests/fixtures/polynomial_t4.tsv");
  CHECK_TRUE(static_cast<bool>(in), "fixture_file_opened");
  const auto a =
      chebref::fit([](long double x) { return x * x * x * x; }, -1, 1, 4);
  auto ex = cheb::cheb_fit([](double x) { return x * x * x * x; },
                           cheb::Interval{-1, 1}, 4);
  std::string line;
  int seen = 0;
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream ss(line);
    int k;
    double exact, tol;
    ss >> k >> exact >> tol;
    // Stored values are the series coefficients directly (endpoint-folded).
    CHECK_NEAR(ex.coeff[k], exact, tol, "fixture_vs_core");
    CHECK_NEAR((double)a[k], exact, tol, "fixture_vs_reference");
    ++seen;
  }
  CHECK_EQ(seen, 5, "fixture_row_count");
}

TEST_CASE("ref.endpoints_exact_under_independent_evaluator") {
  if (!chebref::precision_guard()) {
    std::printf("[ SKIP ] endpoints (no extended long double)\n");
    return;
  }
  const auto a = chebref::fit(
      [](long double x) { return 1.0L / (1.0L + 25.0L * x * x); }, -1, 1, 80);
  const long double fhi = 1.0L / 26.0L;
  CHECK_NEAR((double)std::fabs(chebref::evaluate(a, -1, 1, 1.0L) - fhi),
             0.0, 1e-14, "runge_endpoint_hi");
  CHECK_NEAR((double)std::fabs(chebref::evaluate(a, -1, 1, -1.0L) - fhi),
             0.0, 1e-14, "runge_endpoint_lo");
}

int main() { return chebtest::run_all(); }
