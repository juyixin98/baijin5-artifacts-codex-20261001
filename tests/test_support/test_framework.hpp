#pragma once

// Tiny dependency-free C++20 test framework: concrete value assertions and
// explicit failure-category checks (no "interface is callable" tests).
#include <cmath>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace ptest {

struct TestCase {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<TestCase>& registry() {
  static std::vector<TestCase> cases;
  return cases;
}

struct Registrar {
  Registrar(const std::string& name, std::function<void()> fn) {
    registry().push_back({name, std::move(fn)});
  }
};

struct AssertionFailure : std::runtime_error {
  using std::runtime_error::runtime_error;
};

inline int runAll() {
  int passed = 0;
  int failed = 0;
  for (const auto& tc : registry()) {
    try {
      tc.fn();
      ++passed;
      std::cout << "  PASS " << tc.name << '\n';
    } catch (const AssertionFailure& ex) {
      ++failed;
      std::cout << "  FAIL " << tc.name << ": " << ex.what() << '\n';
    } catch (const std::exception& ex) {
      ++failed;
      std::cout << "  FAIL " << tc.name << ": unexpected exception: "
                << ex.what() << '\n';
    }
  }
  std::cout << (failed == 0 ? "[ok] " : "[FAILED] ") << passed << " passed, "
            << failed << " failed, " << registry().size() << " total\n";
  return failed == 0 ? 0 : 1;
}

}  // namespace ptest

#define PTEST_CONCAT_INNER(a, b) a##b
#define PTEST_CONCAT(a, b) PTEST_CONCAT_INNER(a, b)

#define TEST_CASE(name)                                                  \
  static void PTEST_CONCAT(ptest_fn_, __LINE__)();                      \
  static ::ptest::Registrar PTEST_CONCAT(ptest_reg_, __LINE__){         \
      name, &PTEST_CONCAT(ptest_fn_, __LINE__)};                         \
  static void PTEST_CONCAT(ptest_fn_, __LINE__)()

#define CHECK(cond)                                                         \
  do {                                                                      \
    if (!(cond)) {                                                          \
      throw ::ptest::AssertionFailure(std::string(__FILE__) + ":" +        \
                                      std::to_string(__LINE__) +            \
                                      " CHECK failed: " #cond);             \
    }                                                                       \
  } while (0)

#define CHECK_CLOSE(actual, expected, tol)                                  \
  do {                                                                      \
    double ptest_a = static_cast<double>(actual);                           \
    double ptest_e = static_cast<double>(expected);                         \
    double ptest_d = std::abs(ptest_a - ptest_e);                           \
    double ptest_scale =                                                    \
        std::max(1.0, std::max(std::abs(ptest_a), std::abs(ptest_e)));      \
    if (!(ptest_d <= (tol) * ptest_scale)) {                                \
      std::ostringstream os;                                                \
      os << __FILE__ << ":" << __LINE__                                     \
         << " CHECK_CLOSE failed: " #actual "=" << ptest_a                  \
         << " expected~=" << ptest_e << " diff=" << ptest_d;                \
      throw ::ptest::AssertionFailure(os.str());                            \
    }                                                                       \
  } while (0)

#define CHECK_EQ_STR(actual, expected)                                      \
  do {                                                                      \
    std::string ptest_a = (actual);                                         \
    std::string ptest_e = (expected);                                       \
    if (ptest_a != ptest_e) {                                               \
      throw ::ptest::AssertionFailure(                                      \
          std::string(__FILE__) + ":" + std::to_string(__LINE__) +          \
          " CHECK_EQ_STR failed: '" + ptest_a + "' != '" + ptest_e + "'");  \
    }                                                                       \
  } while (0)
