#pragma once
// Minimal standalone unit-test harness (no external test framework), so the
// test executable is a plain native process.
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

namespace mptest {

struct Case {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<Case>& registry() {
  static std::vector<Case> r;
  return r;
}

struct Registrar {
  Registrar(const std::string& name, std::function<void()> fn) {
    registry().push_back({name, std::move(fn)});
  }
};

struct AssertionError { std::string msg; };

inline int run_all() {
  int failed = 0;
  for (auto& c : registry()) {
    try {
      c.fn();
      std::printf("PASS %s\n", c.name.c_str());
    } catch (const AssertionError& e) {
      std::printf("FAIL %s :: %s\n", c.name.c_str(), e.msg.c_str());
      ++failed;
    } catch (const std::exception& e) {
      std::printf("FAIL %s :: unexpected exception: %s\n", c.name.c_str(), e.what());
      ++failed;
    } catch (...) {
      std::printf("FAIL %s :: unknown exception\n", c.name.c_str());
      ++failed;
    }
  }
  std::printf("----\n%d cases, %d failed\n",
              static_cast<int>(registry().size()), failed);
  return failed == 0 ? 0 : 1;
}
} // namespace mptest

#define MP_TEST(name)                                                            \
  static void name##_body();                                                     \
  static mptest::Registrar name##_reg(#name, name##_body);                       \
  static void name##_body()

#define MP_FAIL_NOW(text)                                                        \
  do {                                                                           \
    std::ostringstream _oss; _oss << text;                                       \
    throw mptest::AssertionError{_oss.str()};                                    \
  } while (0)

#define MP_CHECK(cond)                                                           \
  do { if (!(cond)) MP_FAIL_NOW("check failed: " #cond " @ " __FILE__ ":"        \
                                << __LINE__); } while (0)

#define MP_CHECK_EQ(a, b)                                                        \
  do {                                                                           \
    auto _va = (a); auto _vb = (b);                                              \
    if (!(_va == _vb))                                                           \
      MP_FAIL_NOW(#a " == " #b " got '" << _va << "' vs '" << _vb                \
                  << "' @ " __FILE__ ":" << __LINE__);                           \
  } while (0)

#define MP_CHECK_NE(a, b)                                                        \
  do {                                                                           \
    auto _va = (a); auto _vb = (b);                                              \
    if (_va == _vb)                                                              \
      MP_FAIL_NOW(#a " != " #b " both '" << _va << "' @ " __FILE__ ":"           \
                  << __LINE__);                                                  \
  } while (0)
