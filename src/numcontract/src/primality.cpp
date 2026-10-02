#include "numcontract/primality.h"
#include <initializer_list>

namespace mp::contract {
namespace {
using u64 = uint64_t;
using u128 = __uint128_t;

u64 mulmod(u64 a, u64 b, u64 m) noexcept {
  return static_cast<u64>((static_cast<u128>(a) * b) % m);
}
u64 powmod(u64 a, u64 e, u64 m) noexcept {
  u64 r = 1 % m;
  a %= m;
  while (e) {
    if (e & 1u) r = mulmod(r, a, m);
    a = mulmod(a, a, m);
    e >>= 1u;
  }
  return r;
}
} // namespace

bool is_prime(u64 n) noexcept {
  if (n < 2) return false;
  for (u64 p : {2ULL, 3ULL, 5ULL, 7ULL, 11ULL, 13ULL, 17ULL, 19ULL, 23ULL,
                29ULL, 31ULL, 37ULL}) {
    if (n % p == 0) return n == p;
  }
  u64 d = n - 1;
  int s = 0;
  while ((d & 1u) == 0) { d >>= 1u; ++s; }
  // Deterministic witness set sufficient for the whole uint64 range.
  for (u64 a : {2ULL, 325ULL, 9375ULL, 28178ULL, 450775ULL, 9780504ULL,
                1795265022ULL}) {
    if (a % n == 0) continue;
    u64 x = powmod(a, d, n);
    if (x == 1 || x == n - 1) continue;
    bool composite = true;
    for (int r = 1; r < s; ++r) {
      x = mulmod(x, x, n);
      if (x == n - 1) { composite = false; break; }
    }
    if (composite) return false;
  }
  return true;
}
} // namespace mp::contract
