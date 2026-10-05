#include <exception>
#include <iostream>
#include <string>
#include <vector>

#include "test_framework.hpp"
#include "tests.hpp"

#ifndef GAUSS_LOG_DIR
#define GAUSS_LOG_DIR "logs"
#endif

namespace gauss::test {

int run_all(support::RunLog& log, const std::vector<TestCase>& tests) {
  int total_checks = 0;
  int total_failures = 0;
  for (const auto& test : tests) {
    log.event({{"event", std::string("test_begin")}, {"test", test.name}});
    TestContext ctx(log, test.name);
    try {
      test.run(ctx);
    } catch (const std::exception& e) {
      ctx.check(false, "no exception",
                std::string("uncaught exception: ") + e.what(), __FILE__, __LINE__);
    } catch (...) {
      ctx.check(false, "no exception", "uncaught non-standard exception", __FILE__,
                __LINE__);
    }
    log.event({{"event", std::string("test_end")},
               {"test", test.name},
               {"checks", static_cast<long long>(ctx.checks())},
               {"failures", static_cast<long long>(ctx.failures())}});
    total_checks += ctx.checks();
    total_failures += ctx.failures();
    std::cout << (ctx.failures() == 0 ? "PASS " : "FAIL ") << test.name << " ("
              << ctx.checks() << " checks)\n";
  }
  std::cout << (total_checks - total_failures) << '/' << total_checks
            << " checks passed\n";
  return total_failures == 0 ? 0 : 1;
}

}  // namespace gauss::test

int main() {
  gauss::support::RunLog log(GAUSS_LOG_DIR, "tests");
  const std::vector<gauss::test::TestCase> tests = {
      {"symmetry_weights_positivity_sum",
       gauss::test::test_symmetry_weights_positivity_sum},
      {"interval_mapping", gauss::test::test_interval_mapping},
      {"hand_computed_fixtures", gauss::test::test_hand_computed_fixtures},
      {"cross_check_golub_welsch", gauss::test::test_cross_check_golub_welsch},
      {"polynomial_exactness", gauss::test::test_polynomial_exactness},
      {"exactness_on_mapped_interval",
       gauss::test::test_exactness_on_mapped_interval},
      {"invalid_input", gauss::test::test_invalid_input},
      {"resource_exhaustion", gauss::test::test_resource_exhaustion},
      {"non_convergence_rejected", gauss::test::test_non_convergence_rejected},
      {"state_conflict", gauss::test::test_state_conflict},
  };
  const int rc = gauss::test::run_all(log, tests);
  std::cout << "run_id=" << log.run_id() << "\nlog_file=" << log.path() << '\n';
  return rc;
}
