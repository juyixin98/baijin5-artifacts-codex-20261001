#pragma once

// Minimal dependency-free unit test harness: tracks checks and failures and
// prints a concrete summary. Every CHECK asserts a specific numeric or status
// condition rather than merely invoking the API.

#include <cmath>
#include <iostream>
#include <string>
#include <vector>

namespace proc_test {

struct Stats {
  int checks = 0;
  int failures = 0;
  std::vector<std::string> failure_lines;
};

inline Stats& stats() {
  static Stats s;
  return s;
}

inline void report_fail(const std::string& cond, const char* file,
                        int line_no, const std::string& detail = "") {
  auto& st = stats();
  ++st.failures;
  std::string msg = std::string(file) + ":" + std::to_string(line_no) +
                    " CHECK failed: " + cond +
                    (detail.empty() ? "" : (" | " + detail));
  st.failure_lines.push_back(msg);
  std::cerr << msg << "\n";
}

inline int finish(const std::string& suite) {
  auto& s = stats();
  if (s.failures == 0) {
    std::cout << "[PASS] " << suite << " (" << s.checks << " checks)\n";
    return 0;
  }
  std::cerr << "[FAIL] " << suite << ": " << s.failures << " of "
            << s.checks << " checks failed\n";
  return 1;
}

}  // namespace proc_test

#define CHECK(cond)                                                         \
  do {                                                                      \
    ++proc_test::stats().checks;                                            \
    if (!(cond)) proc_test::report_fail(#cond, __FILE__, __LINE__);        \
  } while (0)

#define CHECK_MSG(cond, detail)                                             \
  do {                                                                      \
    ++proc_test::stats().checks;                                            \
    if (!(cond)) proc_test::report_fail(#cond, __FILE__, __LINE__, detail); \
  } while (0)

#define CHECK_CLOSE(actual, expected, tol)                                  \
  do {                                                                      \
    ++proc_test::stats().checks;                                            \
    double _a = double(actual);                                             \
    double _e = double(expected);                                           \
    if (std::abs(_a - _e) > (tol))                                          \
      proc_test::report_fail(#actual " ~= " #expected, __FILE__, __LINE__,  \
                             "got " + std::to_string(_a) + " expected " +   \
                                 std::to_string(_e));                       \
  } while (0)
