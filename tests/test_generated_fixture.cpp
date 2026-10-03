#include "test_framework.hpp"

#include <cstdio>
#include <sstream>
#include <string>
#include <vector>

#include "cheb/expansion.hpp"
#include "reference/reference.hpp"

#ifndef GEN_FIXTURE_BIN
#error "GEN_FIXTURE_BIN must be defined by CMake"
#endif

using namespace cheb;

// Runs the independent long-double generator as a separate process, parses
// its TSV output, and compares it against the double core.
static std::vector<double> run_generator(const std::string& args) {
  const std::string cmd = std::string(GEN_FIXTURE_BIN) + " " + args;
  FILE* pipe = popen(cmd.c_str(), "r");
  CHECK_TRUE(pipe != nullptr, "generator_pipe_open");
  std::vector<double> values;
  char line[256];
  while (std::fgets(line, sizeof(line), pipe)) {
    std::string s = line;
    if (s.empty() || s[0] == '#') continue;
    std::istringstream ss(s);
    int k = -1;
    long double v = 0.0L;
    ss >> k >> v;
    if (k >= 0) values.push_back(static_cast<double>(v));
  }
  pclose(pipe);
  CHECK_TRUE(values.size() >= 8, "generator_produced_rows");
  return values;
}

TEST_CASE("genfixture.exp32_independent_generator_agrees_with_core") {
  if (!chebref::precision_guard()) {
    std::printf("[ SKIP ] genfixture exp32 (no extended long double)\n");
    return;
  }
  const auto ref = run_generator("exp 32");
  auto ex = cheb_fit([](double t) { return std::exp(t); },
                     Interval{-1, 1}, 32);
  for (size_t k = 0; k < ref.size(); ++k) {
    const double scale = std::max(1.0, std::fabs(ref[k]));
    CHECK_TRUE(std::fabs(ex.coeff[k] - ref[k]) < 1e-12 * scale,
               "exp32_core_vs_generated_row");
  }
}

TEST_CASE("genfixture.abs64_analytic_sign_pattern_and_magnitude") {
  if (!chebref::precision_guard()) {
    std::printf("[ SKIP ] genfixture abs64 (no extended long double)\n");
    return;
  }
  const auto ref = run_generator("abs 64");
  CHECK_NEAR(ref[0], 2.0 / M_PI, 3e-3, "gen_abs_c0");
  CHECK_NEAR(ref[2], 4.0 / (3.0 * M_PI), 3e-3, "gen_abs_c2");
  for (size_t k = 1; k < ref.size(); k += 2)
    CHECK_NEAR(ref[k], 0.0, 1e-9, "gen_abs_odd_coeffs_zero");
}

int main() { return chebtest::run_all(); }
