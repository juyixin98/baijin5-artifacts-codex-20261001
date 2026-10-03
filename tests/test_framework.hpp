#pragma once
// Minimal self-contained test harness (no external test runtime).
// Records concrete pass/fail counts and returns a non-zero exit code on any
// failure so CTest reports a real result.
#include <cmath>
#include <complex>
#include <cstdio>
#include <string>
#include <vector>

namespace tf {

struct Case {
  std::string name;
  bool passed = true;
  std::string detail;
};

inline std::vector<Case>& cases() {
  static std::vector<Case> c;
  return c;
}

inline void begin(const std::string& name) { cases().push_back({name, true, {}}); }
inline Case& cur() { return cases().back(); }

inline void fail(const std::string& why) {
  cur().passed = false;
  if (cur().detail.empty()) cur().detail = why;
}

inline void check(bool cond, const std::string& why) {
  if (!cond) fail(why);
}

template <class T>
inline void checkEq(T actual, T expected, const std::string& why) {
  if (!(actual == expected)) {
    char buf[160];
    std::snprintf(buf, sizeof(buf), "%s (actual=%lld expected=%lld)",
                  why.c_str(), static_cast<long long>(actual),
                  static_cast<long long>(expected));
    fail(buf);
  }
}

inline void checkCloseAbs(std::complex<double> a, std::complex<double> b,
                          double tol, const std::string& what) {
  const double e = std::abs(a - b);
  if (!(e <= tol)) {
    char buf[256];
    std::snprintf(buf, sizeof(buf),
                  "%s: |got-re|=%.6g exceeds tol %.6g (got %.12g%+.12gi "
                  "want %.12g%+.12gi)",
                  what.c_str(), e, tol, a.real(), a.imag(), b.real(), b.imag());
    fail(buf);
  }
}

inline int finish() {
  int npass = 0, nfail = 0;
  for (const auto& c : cases()) {
    std::printf("[%s] %s", c.passed ? "PASS" : "FAIL", c.name.c_str());
    if (!c.passed) std::printf("  -> %s", c.detail.c_str());
    std::printf("\n");
    c.passed ? ++npass : ++nfail;
  }
  std::printf("---- %d passed, %d failed ----\n", npass, nfail);
  return nfail == 0 ? 0 : 1;
}

} // namespace tf
