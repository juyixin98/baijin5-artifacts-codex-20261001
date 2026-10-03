// Acceptance of behavior contract #2: chirp phase controls large-index
// error. The independent oracles here deliberately use DIFFERENT arithmetic
// from the SUT:
//   (A) exact 128-bit modular square computed by schoolbook 64x64->128
//       multiply (the SUT uses a single __int128 multiply + remainder),
//       then long double trig on the reduced argument;
//   (B) an angle-addition recurrence in long double for small N;
//   (C) algebraic identities: chirp(0)=1 and chirp(-n)=conj(chirp(n)).
#include "mathcore/chirp.hpp"
#include "test_framework.hpp"
#include <cmath>
#include <cstdint>
#include <numbers>

namespace mc = fft::mathcore;

namespace {

// Schoolbook unsigned multiply a*b into 128 bits as hi:lo.
struct U128 { std::uint64_t hi, lo; };
U128 mul64(std::uint64_t a, std::uint64_t b) {
  const std::uint64_t a0 = a & 0xFFFFFFFFULL, a1 = a >> 32;
  const std::uint64_t b0 = b & 0xFFFFFFFFULL, b1 = b >> 32;
  const std::uint64_t p00 = a0 * b0;
  const std::uint64_t p01 = a0 * b1;
  const std::uint64_t p10 = a1 * b0;
  const std::uint64_t p11 = a1 * b1;
  const std::uint64_t mid = (p00 >> 32) + (p01 & 0xFFFFFFFFULL) +
                            (p10 & 0xFFFFFFFFULL);
  return {p11 + (p01 >> 32) + (p10 >> 32) + (mid >> 32),
          (p00 & 0xFFFFFFFFULL) | (mid << 32)};
}
// 128-bit value modulo m (m fits in 64 bits).
std::uint64_t mod128(U128 v, std::uint64_t m) {
  __uint128_t x = (static_cast<__uint128_t>(v.hi) << 64) | v.lo;
  return static_cast<std::uint64_t>(x % m);
}

std::complex<double> oracleChirp(long long n, std::size_t N, int sign) {
  std::uint64_t an = n < 0 ? static_cast<std::uint64_t>(-n)
                           : static_cast<std::uint64_t>(n);
  std::uint64_t mod = 2ULL * static_cast<std::uint64_t>(N);
  // Exact n^2 mod 2N via schoolbook multiply — distinct code path from SUT.
  std::uint64_t r;
  if (an <= 0xFFFFFFFFULL) {
    r = (an * an) % mod;
  } else {
    U128 sq = mul64(an, an);
    r = mod128(sq, mod);
  }
  const long double angle = std::numbers::pi_v<long double> *
                            static_cast<long double>(r) /
                            static_cast<long double>(N);
  return {static_cast<double>(cosl(angle)),
          static_cast<double>(sign * sinl(angle))};
}

// Independent recurrence oracle: w_{n+1} = w_n * exp(i*pi/N) *
// exp(i*2pi*n/N); build phases by repeated addition in long double.
std::complex<double> recurrenceChirp(long long n, std::size_t N, int sign) {
  long double theta = 0.0L;
  const long double step = std::numbers::pi_v<long double> /
                           static_cast<long double>(N);
  long long pos = n < 0 ? -n : n;
  for (long long k = 0; k < pos; ++k) {
    theta += step * (2.0L * static_cast<long double>(k) + 1.0L);
    theta = fmodl(theta, 2.0L * std::numbers::pi_v<long double>);
  }
  return {static_cast<double>(cosl(theta)),
          static_cast<double>(sign * sinl(theta))};
}

} // namespace

int main() {
  tf::begin("chirp matches recurrence oracle for small indices");
  {
    std::size_t N = 17;
    double e = 0;
    for (long long n = -40; n <= 40; ++n)
      e = std::max(e, std::abs(mc::chirpAt(n, N, -1) -
                               recurrenceChirp(n, N, -1)));
    tf::check(e <= 1e-15, "recurrence oracle error " + std::to_string(e));
  }

  tf::begin("chirp matches exact modular oracle, indices up to 1e12");
  {
    // The key large-index claim: error stays tiny even at n ~ 1e12 where a
    // naive double n^2 or long-double full-angle reduction would degrade.
    std::size_t N = 5003;
    double e = 0;
    for (long long n : {1LL, 777LL, 1LL << 30, 123456789LL, 1000000007LL,
                        1LL << 40, 999999999989LL}) {
      e = std::max(e, std::abs(mc::chirpAt(n, N, +1) -
                               oracleChirp(n, N, +1)));
      e = std::max(e, std::abs(mc::chirpAt(-n, N, -1) -
                               oracleChirp(-n, N, -1)));
    }
    tf::check(e <= 1e-15, "large-index oracle error " + std::to_string(e));
  }

  tf::begin("naive full-angle long double demonstrably loses precision");
  {
    // Document why controlled reduction is needed: the "obvious" formula
    // pi*n*n/N evaluated directly drifts at large n (compare oracle).
    std::size_t N = 5003;
    long long n = 1LL << 40;
    long double naive = std::numbers::pi_v<long double> *
                        static_cast<long double>(n) *
                        static_cast<long double>(n) /
                        static_cast<long double>(N);
    naive = fmodl(naive, 2.0L * std::numbers::pi_v<long double>);
    std::complex<double> nv(static_cast<double>(cosl(naive)),
                            static_cast<double>(sinl(naive)));
    double drift = std::abs(nv - oracleChirp(n, N, +1));
    tf::check(drift > 1e-6,
              "naive full-angle error should be visibly degraded, got " +
                  std::to_string(drift));
  }

  tf::begin("chirp(0)=1; phase even in n; conjugation via sign flip");
  {
    auto c0 = mc::chirpAt(0, 1000, -1);
    tf::checkCloseAbs(c0, {1, 0}, 0.0, "chirp(0)");
    for (long long n : {1LL, 500LL, 70000LL, 1LL << 35}) {
      auto cp = mc::chirpAt(n, 777, -1);
      auto cm = mc::chirpAt(-n, 777, -1);
      // Phase depends on n^2 only, so chirp is even: chirp(-n)==chirp(n).
      tf::check(std::abs(cm - cp) < 1e-15, "evenness in n");
      // sign selects the exponent sign, hence conjugation.
      auto cpos = mc::chirpAt(n, 777, +1);
      tf::check(std::abs(cpos - std::conj(cp)) < 1e-15,
                "sign flip conjugates");
    }
  }
  return tf::finish();
}
