#pragma once
// Independent pointwise Horner reference used by tests. It shares only the
// Boost cpp_int digit type and re-implements parsing/evaluation without calling
// any core polynomial, product-tree, or remainder-tree routine. Expected
// outputs are therefore never produced by the system under test.
#include <boost/multiprecision/cpp_int.hpp>
#include <cstdint>
#include <string>
#include <vector>

namespace mptest::ref {
using boost::multiprecision::cpp_int;

inline std::string horner_integer(const std::vector<std::string>& coeff_hi,
                                  const std::string& point) {
  cpp_int x(point);
  cpp_int y = 0;
  bool started = false;
  for (const auto& t : coeff_hi) {
    cpp_int a(t);
    y = started ? y * x + a : a;
    started = true;
  }
  return y.str();
}

inline std::string horner_field(const std::vector<std::string>& coeff_hi,
                                const std::string& point, uint64_t mod) {
  auto red = [&](const std::string& t) -> uint64_t {
    cpp_int v(t);
    cpp_int r = v % cpp_int(mod);
    if (r < 0) r += mod;
    return r.convert_to<uint64_t>();
  };
  uint64_t x = red(point);
  uint64_t y = 0;
  bool started = false;
  for (const auto& t : coeff_hi) {
    uint64_t a = red(t);
    uint64_t prod =
        static_cast<uint64_t>((static_cast<__uint128_t>(y) * x) % mod);
    y = started ? prod + a : a;
    if (y >= mod) y -= mod;
    started = true;
  }
  return std::to_string(y);
}

inline cpp_int eval_poly(const std::vector<cpp_int>& f_low, const cpp_int& x) {
  if (f_low.empty()) return 0;
  cpp_int y = f_low.back();
  for (size_t i = f_low.size() - 1; i-- > 0;) y = y * x + f_low[i];
  return y;
}
} // namespace mptest::ref
