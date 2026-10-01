// SPDX-License-Identifier: MIT
// Tiny independent test harness with concrete assertions.
#pragma once

#include <cmath>
#include <complex>
#include <iostream>
#include <string>
#include <vector>

namespace tst {

struct Case { std::string name; void(*fn)(); };
inline std::vector<Case>& registry() { static std::vector<Case> r; return r; }

inline int& failures() { static int f = 0; return f; }
inline int& checks()   { static int c = 0; return c; }

struct Registrar {
  Registrar(const char* name, void(*fn)()) {
    registry().push_back({name, fn});
  }
};

inline bool near(double a, double b, double tol) {
  return std::abs(a - b) <= tol;
}

} // namespace tst

#define TST_CASE(name)                                                      \
  static void name();                                                       \
  static ::tst::Registrar reg_##name(#name, name);                         \
  static void name()

#define CHECK(cond)                                                         \
  do {                                                                      \
    ++::tst::checks();                                                      \
    if (!(cond)) {                                                          \
      ++::tst::failures();                                                  \
      std::cerr << "FAIL [" << __FILE__ << ":" << __LINE__                  \
                << "] CHECK false: " #cond "\n";                            \
    }                                                                       \
  } while (0)

#define CHECK_EQ_CODE(actual, expected)                                     \
  do {                                                                      \
    ++::tst::checks();                                                      \
    auto _a = (actual);                                                     \
    auto _e = (expected);                                                   \
    if (!(_a == _e)) {                                                      \
      ++::tst::failures();                                                  \
      std::cerr << "FAIL [" << __FILE__ << ":" << __LINE__                  \
                << "] error code mismatch: got "                           \
                << static_cast<int>(_a) << " expected "                    \
                << static_cast<int>(_e) << " (" #actual ")\n";             \
    }                                                                       \
  } while (0)

#define CHECK_CABS_NEAR(actual, expected, tol)                              \
  do {                                                                      \
    ++::tst::checks();                                                      \
    auto _a = (actual); auto _b = (expected);                              \
    double _d = std::abs(_a - _b);                                         \
    if (_d > (tol)) {                                                       \
      ++::tst::failures();                                                  \
      std::cerr << "FAIL [" << __FILE__ << ":" << __LINE__                  \
                << "] complex mismatch: |" << _a << " - " << _b             \
                << "| = " << _d << " > tol " << (tol) << "\n";              \
    }                                                                       \
  } while (0)
