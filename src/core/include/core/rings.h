#pragma once
// Ring digit operations shared by every kernel algorithm.
//
// Two domains are supported and must never be mixed within one evaluation
// (the numeric contract enforces that before the kernel is entered):
//   * IntegerOps: exact unbounded integers (Boost::multiprecision::cpp_int).
//   * FieldOps:   Z/pZ for a runtime prime p < 2^63, digits stored in [0,p).
#include <boost/multiprecision/cpp_int.hpp>
#include <cstdint>
#include <string>

namespace mp::core {

using boost::multiprecision::cpp_int;

struct IntegerOps {
  using D = cpp_int;
  static constexpr bool kModular = false;

  static D zero() { return D(0); }
  static D one()  { return D(1); }
  static bool is_zero(const D& a) { return a == 0; }
  static void add(D& r, const D& a, const D& b) { r = a + b; }
  static void sub(D& r, const D& a, const D& b) { r = a - b; }
  static void neg(D& r, const D& a) { r = -a; }
  static void mul(D& r, const D& a, const D& b) { r = a * b; }
  static void ax(D& r, const D& a, const D& x, const D& b) { r = a * x + b; }
  static std::string str(const D& a) { return a.str(); }
  static bool parse(const std::string& tok, D& out) {
    try { out = D(tok); return true; } catch (...) { return false; }
  }
  static const char* digit_name() { return "cpp_int"; }
};

class FieldOps {
public:
  using D = std::uint64_t;
  static constexpr bool kModular = true;

  explicit FieldOps(uint64_t p) : p_(p) {}
  uint64_t modulus() const noexcept { return p_; }

  static D zero() { return 0; }
  D one() const { return 1 % p_; }
  static bool is_zero(D a) { return a == 0; }

  void add(D& r, D a, D b) const {
    uint64_t s = a + b; // safe: a,b < p < 2^63
    r = (s >= p_ || s < a) ? s - p_ : s;
  }
  void sub(D& r, D a, D b) const { r = a >= b ? a - b : a + p_ - b; }
  void neg(D& r, D a) const { r = a == 0 ? 0 : p_ - a; }
  void mul(D& r, D a, D b) const {
    r = static_cast<uint64_t>((static_cast<__uint128_t>(a) * b) % p_);
  }
  // r = a*x + b mod p (Horner step fused to avoid a second reduction).
  void ax(D& r, D a, D x, D b) const { mul(r, a, x); add(r, r, b); }

  std::string str(D a) const { return std::to_string(a); }
  bool parse(const std::string& tok, D& out) const {
    // Sign-aware modular reduction of the authored literal.
    bool neg = false;
    size_t i = 0;
    if (!tok.empty() && (tok[0] == '-' || tok[0] == '+')) { neg = tok[0] == '-'; i = 1; }
    if (i == tok.size()) return false;
    uint64_t v = 0;
    for (; i < tok.size(); ++i) {
      if (tok[i] < '0' || tok[i] > '9') return false;
      v = static_cast<uint64_t>((static_cast<__uint128_t>(v) * 10 +
                                 static_cast<unsigned>(tok[i] - '0')) % p_);
    }
    out = neg && v != 0 ? p_ - v : v;
    return true;
  }
  static const char* digit_name() { return "uint64_mod_p"; }

private:
  uint64_t p_;
};

} // namespace mp::core
