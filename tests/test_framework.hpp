// Tiny self-contained test framework: concrete assertions with failure
// categories, no external test runtime required.
#ifndef CHEB_TEST_FRAMEWORK_HPP
#define CHEB_TEST_FRAMEWORK_HPP

#include <cmath>
#include <cstdio>
#include <cstdlib>
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

struct AssertionFailure {
  std::string message;
};

struct Registrar {
  Registrar(const std::string& name, std::function<void()> fn) {
    registry().push_back({name, std::move(fn)});
  }
};

inline int run_all() {
  int failed = 0;
  for (const auto& c : registry()) {
    try {
      c.fn();
      std::printf("[ PASS ] %s\n", c.name.c_str());
    } catch (const AssertionFailure& f) {
      ++failed;
      std::printf("[ FAIL ] %s :: %s\n", c.name.c_str(), f.message.c_str());
    } catch (const std::exception& e) {
      ++failed;
      std::printf("[ FAIL ] %s :: unexpected exception: %s\n", c.name.c_str(),
                  e.what());
    }
  }
  std::printf("%zu checks, %d failed\n", registry().size(), failed);
  return failed == 0 ? 0 : 1;
}

}  // namespace chebtest

#define CHEB_CONCAT2(a, b) a##b
#define CHEB_CONCAT1(a, b) CHEB_CONCAT2(a, b)
#define CHEB_CONCAT(a, b) CHEB_CONCAT1(a, b)

#define TEST_CASE(name)                                                \
  static void CHEB_CONCAT(cheb_test_fn_, __LINE__)();                  \
  static ::chebtest::Registrar CHEB_CONCAT(cheb_reg_, __LINE__)(       \
      name, CHEB_CONCAT(cheb_test_fn_, __LINE__));                     \
  static void CHEB_CONCAT(cheb_test_fn_, __LINE__)()

#define FAIL_CAT(category, msg)                                    \
  throw ::chebtest::AssertionFailure{std::string(category) + ": " + (msg)}

#define CHECK_TRUE(cond, category)                              \
  do {                                                           \
    if (!(cond)) FAIL_CAT(category, "CHECK_TRUE(" #cond ")");    \
  } while (0)

#define CHECK_EQ(actual, expected, category)                           \
  do {                                                                   \
    if (!((actual) == (expected)))                                       \
      FAIL_CAT(category, "CHECK_EQ(" #actual " == " #expected ")");      \
  } while (0)

#define CHECK_NEAR(actual, expected, tol, category)             \
  do {                                                          \
    const double _a = static_cast<double>(actual);              \
    const double _e = static_cast<double>(expected);            \
    const double _d = std::fabs(_a - _e);                       \
    if (_d > (tol) && _d > (tol) * std::fabs(_e))               \
      FAIL_CAT(category, "CHECK_NEAR(" #actual " ~ " #expected  \
                         ") got=" + std::to_string(_a) +        \
                         " want=" + std::to_string(_e) +         \
                         " diff=" + std::to_string(_d));         \
  } while (0)

#define CHECK_THROWS_STD(expr, category)                        \
  do {                                                          \
    bool _threw = false;                                        \
    try {                                                       \
      (void)(expr);                                             \
    } catch (const std::exception&) {                           \
      _threw = true;                                            \
    }                                                           \
    if (!_threw) FAIL_CAT(category, "expected exception: " #expr); \
  } while (0)

#endif
