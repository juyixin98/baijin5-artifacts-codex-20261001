// Independent benchmark runner: cross-checks the Newton kernel against the
// Golub-Welsch eigensolver reference over a range of orders, prints a table
// and logs every row as a structured event. Exit code is non-zero if any
// order disagrees beyond tolerance.

#include <cstdio>
#include <string>
#include <vector>

#include "benchmark/golub_welsch.hpp"
#include "run_log.hpp"

#ifndef GAUSS_LOG_DIR
#define GAUSS_LOG_DIR "logs"
#endif

int main() {
  gauss::support::RunLog log(GAUSS_LOG_DIR, "benchmark");
  const std::vector<std::size_t> orders = {2, 3, 5, 8, 13, 21, 32, 64, 128, 256};

  std::printf("%-6s %-14s %-14s %-12s %-12s %s\n", "n", "max|dx|", "max|dw|",
              "kernel_ms", "golub_ms", "verdict");
  bool all_passed = true;
  for (const std::size_t n : orders) {
    const gauss::benchmark::CrossCheck cc =
        gauss::benchmark::cross_check_legendre(n);
    all_passed = all_passed && cc.passed;
    std::printf("%-6zu %-14.3e %-14.3e %-12.3f %-12.3f %s\n", n,
                cc.max_node_abs_diff, cc.max_weight_abs_diff,
                cc.kernel_seconds * 1e3, cc.golub_welsch_seconds * 1e3,
                cc.passed ? "PASS" : "FAIL");
    log.event({{"event", std::string("cross_check")},
               {"order", static_cast<long long>(cc.order)},
               {"max_node_abs_diff", cc.max_node_abs_diff},
               {"max_weight_abs_diff", cc.max_weight_abs_diff},
               {"node_tolerance", cc.node_tolerance},
               {"weight_tolerance", cc.weight_tolerance},
               {"kernel_seconds", cc.kernel_seconds},
               {"golub_welsch_seconds", cc.golub_welsch_seconds},
               {"passed", cc.passed},
               {"judgment",
                std::string("kernel vs independent Golub-Welsch eigensolver")}});
  }
  std::printf("run_id=%s\nlog_file=%s\n", log.run_id().c_str(),
              log.path().c_str());
  std::printf("%s\n", all_passed ? "BENCHMARK PASS" : "BENCHMARK FAIL");
  return all_passed ? 0 : 1;
}
