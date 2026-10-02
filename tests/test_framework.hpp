// SPDX-License-Identifier: MIT
// Tiny self-contained test framework: every assertion records a concrete
// failure category so a failed run states WHAT was violated, not merely that
// something failed.
#ifndef CHEBCORE_TEST_FRAMEWORK_HPP
#define CHEBCORE_TEST_FRAMEWORK_HPP

#include <cmath>
#include <cstdio>
#include <exception>
#include <functional>
#include <string>
#include <vector>

namespace chebtest {

struct Case {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<Case>& registry() {
  static std::vector<Case> r;
  return r;
}

struct AssertionFailure : std::exception {
  std::string message;
  explicit AssertionFailure(std::string m) : message(std::move(m)) {}
  const char* what() const noexcept override { return message.c_str(); }
};

struct Registrar {
  Registrar(const std::string& name, std::function<void()> fn) {
    registry().push_back(Case{name, std::move(fn)});
  }
};

inline void fail(const std::string& category, const std::string& detail) {
  throw AssertionFailure("[" + category + "] " + detail);
}

inline int run_all() {
  int failed = 0;
  for (const auto& c : registry()) {
    try {
      c.fn();
      std::printf("PASS %s\n", c.name.c_str());
    } catch (const AssertionFailure& e) {
      ++failed;
      std::printf("FAIL %s :: %s\n", c.name.c_str(), e.what());
    } catch (const std::exception& e) {
      ++failed;
      std::printf("FAIL %s :: [UNEXPECTED-EXCEPTION] %s\n", c.name.c_str(),
                  e.what());
    } catch (...) {
      ++failed;
      std::printf("FAIL %s :: [UNEXPECTED-EXCEPTION] unknown\n", c.name.c_str());
    }
  }
  std::printf("----\n%d/%d cases passed, %d failed\n",
              static_cast<int>(registry().size()) - failed,
              static_cast<int>(registry().size()), failed);
  return failed == 0 ? 0 : 1;
}

}  // namespace chebtest

#define TEST_CONCAT2(a, b) a##b
#define TEST_CONCAT(a, b) TEST_CONCAT2(a, b)
#define TEST_CASE(name)                                                     \
  static void TEST_CONCAT(test_fn_, __LINE__)();                            \
  static ::chebtest::Registrar TEST_CONCAT(test_reg_, __LINE__)(            \
      name, &TEST_CONCAT(test_fn_, __LINE__));                              \
  static void TEST_CONCAT(test_fn_, __LINE__)()

#define REQUIRE(cond)                                                       \
  do {                                                                      \
    if (!(cond))                                                            \
      ::chebtest::fail("REQUIRE", std::string(#cond) + " at " __FILE__ ":"  \
                       + std::to_string(__LINE__));                         \
  } while (0)

#define REQUIRE_CLOSE(actual, expected, eps_rel)                            \
  do {                                                                      \
    double _a = static_cast<double>(actual);                                \
    double _e = static_cast<double>(expected);                              \
    double _tol = (eps_rel) * std::max(1.0, std::fabs(_e));                 \
    if (!(std::fabs(_a - _e) <= _tol))                                      \
      ::chebtest::fail(                                                     \
          "NUMERIC-MISMATCH",                                               \
          std::string(#actual) + "=" + std::to_string(_a) + " expected " +  \
              std::to_string(_e) + " (rel " #eps_rel ") at " __FILE__ ":" + \
              std::to_string(__LINE__));                                    \
  } while (0)

#define REQUIRE_ABS(actual, expected, eps_abs)                              \
  do {                                                                      \
    double _a = static_cast<double>(actual);                                \
    double _e = static_cast<double>(expected);                              \
    if (!(std::fabs(_a - _e) <= (eps_abs)))                                 \
      ::chebtest::fail(                                                     \
          "NUMERIC-MISMATCH",                                               \
          std::string(#actual) + "=" + std::to_string(_a) + " expected " +  \
              std::to_string(_e) + " (abs " #eps_abs ") at " __FILE__ ":" + \
              std::to_string(__LINE__));                                    \
  } while (0)

#define REQUIRE_THROWS_STD(kind, expr)                                      \
  do {                                                                      \
    bool _threw = false;                                                    \
    try {                                                                   \
      expr;                                                                 \
    } catch (const kind&) {                                                 \
      _threw = true;                                                        \
    } catch (...) {                                                         \
      ::chebtest::fail(                                                     \
          "WRONG-EXCEPTION-TYPE",                                           \
          std::string(#expr) + " threw a non-" #kind " at " __FILE__ ":" +  \
              std::to_string(__LINE__));                                    \
    }                                                                      \
    if (!_threw)                                                            \
      ::chebtest::fail("MISSING-EXCEPTION",                                 \
                       std::string(#expr) + " did not throw " #kind         \
                       " at " __FILE__ ":" + std::to_string(__LINE__));     \
  } while (0)

#define REQUIRE_EQ(actual, expected)                                        \
  do {                                                                      \
    if (!((actual) == (expected)))                                          \
      ::chebtest::fail(                                                     \
          "EQUALITY",                                                       \
          std::string(#actual) + " != " #expected " at " __FILE__ ":" +     \
              std::to_string(__LINE__));                                    \
  } while (0)

#endif

#define REQUIRE_NOTHROW(expr)                                                 \
  do {                                                                       \
    try {                                                                    \
      expr;                                                                  \
    } catch (...) {                                                          \
      ::chebtest::fail(                                                      \
          "UNEXPECTED-EXCEPTION",                                            \
          std::string(#expr) + " threw at " __FILE__ ":" +                   \
              std::to_string(__LINE__));                                     \
    }                                                                        \
  } while (0)
