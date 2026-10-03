#include "polyeval/field.hpp"

#include <charconv>
#include <cstring>
#include <string>

namespace polyeval::field {

u64& modulus_slot() noexcept {
  static thread_local u64 p = kInvalidModulus;
  return p;
}
u64 modulus() {
  u64 p = modulus_slot();
  if (p == kInvalidModulus) {
    throw std::logic_error("polyeval::field modulus not initialized (use ModIntScope)");
  }
  return p;
}
void set_modulus(u64 p) {
  if (p < 2) throw std::invalid_argument("field modulus must be a prime >= 2");
  modulus_slot() = p;
}

namespace {
u64 mulmod_u64(u64 a, u64 b, u64 m) noexcept {
  return static_cast<u64>((static_cast<u128>(a) * b) % m);
}
u64 powmod_u64(u64 a, u64 e, u64 m) noexcept {
  u64 r = 1 % m;
  a %= m;
  while (e) {
    if (e & 1u) r = mulmod_u64(r, a, m);
    a = mulmod_u64(a, a, m);
    e >>= 1;
  }
  return r;
}
}  // namespace

bool is_prime_u64(u64 n) noexcept {
  if (n < 2) return false;
  // Deterministic witness set valid for the whole 64-bit range.
  static const u64 small_primes[] = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37};
  for (u64 q : small_primes) {
    if (n == q) return true;
    if (n % q == 0) return false;
  }
  u64 d = n - 1;
  unsigned s = 0;
  while ((d & 1u) == 0) { d >>= 1; ++s; }
  static const u64 bases[] = {
      2ULL, 325ULL, 9375ULL, 28178ULL, 450775ULL, 9780504ULL, 1795265022ULL};
  for (u64 a : bases) {
    if (a % n == 0) continue;
    u64 x = powmod_u64(a, d, n);
    if (x == 1 || x == n - 1) continue;
    bool composite = true;
    for (unsigned r = 1; r < s; ++r) {
      x = mulmod_u64(x, x, n);
      if (x == n - 1) { composite = false; break; }
    }
    if (composite) return false;
  }
  return true;
}

u64 parse_u64(std::string_view s) {
  u64 v = 0;
  auto res = std::from_chars(s.data(), s.data() + s.size(), v);
  if (res.ec != std::errc{} || res.ptr != s.data() + s.size()) {
    throw std::invalid_argument(std::string("invalid unsigned integer: '") + std::string(s) + "'");
  }
  return v;
}

ModInt parse_canonical(std::string_view s, u64 p) {
  u64 v = parse_u64(s);
  if (v >= p) {
    throw std::invalid_argument(std::string("field element out of canonical range [0, p): '") +
                                std::string(s) + "'");
  }
  return ModInt::raw(v);
}

}  // namespace polyeval::field
