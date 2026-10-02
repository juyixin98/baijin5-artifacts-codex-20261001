#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>

#include "test_framework.hpp"

#ifndef PADE_VERSION
#define PADE_VERSION "0.0.0-dev"
#endif

namespace pade_test {

int run_all(const std::string& run_id, const std::string& version) {
    std::cout << "pade-test run_id=" << run_id << " version=" << version
              << " cases=" << registry().size() << "\n";
    int failedCases = 0;
    int idx = 0;
    for (auto& c : registry()) {
        ++idx;
        stats().current = c.name;
        std::cout << "[ " << idx << "/" << registry().size() << "] "
                  << c.name << " ...\n";
        int before = stats().failures;
        try {
            c.fn();
        } catch (const std::exception& e) {
            report(false, "no exception expected", __FILE__, __LINE__, e.what());
        }
        if (stats().failures > before) ++failedCases;
    }
    std::cout << "----------------------------------------\n";
    std::cout << "checks=" << stats().checks
              << " failures=" << stats().failures
              << " failed_cases=" << failedCases << "\n";
    if (stats().failures) {
        std::cout << "FAILED assertions:\n";
        for (auto& m : stats().failure_msgs) std::cout << "  " << m << "\n";
    }
    std::cout << (stats().failures == 0 ? "VERDICT PASS" : "VERDICT FAIL")
              << "\n";
    return stats().failures == 0 ? 0 : 1;
}

} // namespace pade_test

int main() {
    using namespace std::chrono;
    auto ms = duration_cast<milliseconds>(
                  system_clock::now().time_since_epoch()).count();
    const char* env = std::getenv("PADE_RUN_ID");
    std::string rid = env ? env : ("test-" + std::to_string(ms));
    return pade_test::run_all(rid, PADE_VERSION);
}
