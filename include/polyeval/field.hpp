#pragma once
// Field (modular) arithmetic over F_p for a runtime-set prime p < 2^63.
// The modulus is process-global via an RAII scope (ModIntScope), which keeps
// the type usable inside Eigen fixed-size expressions and std::vector.
#include <cstdint>
#include <compare>
#include <ostream>
#include <stdexcept>
#include <string_view>

namespace polyeval::field {

using u64 = std::uint64_t;
using u128 = __uint128_t;

inline constexpr u64 kInvalidModulus = 0;

u64& modulus_slot() noexcept;
u64 modulus();
void set_modulus(u64 p);

// Deterministic primality check, exact for all 64-bit unsigned integers
// (deterministic Miller-Rabin witness set).
bool is_prime_u64(u64 n) noexcept;

struct ModIntScope {
  u64 previous;
  explicit ModIntScope(u64 p) : previous(modulus_slot()) { set_modulus(p); }
  ~ModIntScope() { modulus_slot() = previous; }
  ModIntScope(const ModIntScope&) = delete;
  ModIntScope& operator=(const ModIntScope&) = delete;
};

class ModInt {
 public:
  u64 v = 0;

  ModInt() = default;
  explicit ModInt(u64 x) noexcept : v(x % modulus()) {}

  static ModInt raw(u64 x) noexcept { ModInt r; r.v = x; return r; }

  ModInt operator+(ModInt o) const noexcept {
    u64 x = v + o.v;
    u64 p = modulus();
    return raw(x >= p || x < v ? x - p : x); // x < v catches overflow
  }
  ModInt operator-(ModInt o) const noexcept {
    u64 p = modulus();
    return raw(v >= o.v ? v - o.v : v + p - o.v);
  }
  ModInt operator-() const noexcept { return raw(v == 0 ? 0 : modulus() - v); }
  ModInt operator*(ModInt o) const noexcept {
    return raw(static_cast<u64>((static_cast<u128>(v) * o.v) % modulus()));
  }
  ModInt& operator+=(ModInt o) noexcept { *this = *this + o; return *this; }
  ModInt& operator-=(ModInt o) noexcept { *this = *this - o; return *this; }
  ModInt& operator*=(ModInt o) noexcept { *this = *this * o; return *this; }

  bool operator==(const ModInt&) const = default;
};

inline std::ostream& operator<<(std::ostream& os, ModInt m) { return os << m.v; }

// Parse a non-negative canonical residue 0 <= s < p. Throws std::invalid_argument.
ModInt parse_canonical(std::string_view s, u64 p);
u64 parse_u64(std::string_view s);

}  // namespace polyeval::field
