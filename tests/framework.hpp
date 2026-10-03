#pragma once
// Dependency-free micro test framework: asserts concrete values and records
// the exact failure category (not just "interface is callable").
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <string>
#include <vector>

namespace pe_test {

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

struct AssertionError {
  std::string msg;
};

inline void check(bool cond, const std::string& msg) {
  if (!cond) throw AssertionError{msg};
}

template <class A, class B>
void expect_eq(const A& a, const B& b, const std::string& ctx) {
  if (!(a == b)) {
    throw AssertionError{ctx + " got=" + std::to_string(a) + " want=" + std::to_string(b)};
  }
}

inline void expect_str(const std::string& a, const std::string& b, const std::string& ctx) {
  if (a != b) throw AssertionError{ctx + " got='" + a + "' want='" + b + "'"};
}

}  // namespace pe_test

#define TEST_CASE(NAME) \
  static void NAME(); \
  static pe_test::Registrar reg_##NAME(#NAME, NAME); \
  static void NAME()
