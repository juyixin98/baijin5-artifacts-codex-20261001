#include "chirp.hpp"
#include <cmath>
#include <numbers>

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpedantic"
#endif

namespace fft::mathcore {

// Large-index error control:
//   pi*n^2/N mod 2pi. We reduce n^2 modulo 2N in 128-bit integer arithmetic
//   (exact for all realistic N), then convert to long double and reduce
//   modulo 2pi. The trig argument is therefore always within [0,2pi), no
//   matter how large n is, avoiding precision loss in n^2 and giant-angle
//   argument reduction inside libm.
std::complex<double> chirpAt(std::int64_t n, std::size_t N, int sign) {
  const __int128 nn = static_cast<__int128>(n < 0 ? -n : n);
  const __int128 mod = static_cast<__int128>(2) * static_cast<__int128>(N);
  const __int128 rr = (nn * nn) % mod;
  long double angle = std::numbers::pi_v<long double> *
                      static_cast<long double>(rr) /
                      static_cast<long double>(N);
  angle = fmodl(angle, 2.0L * std::numbers::pi_v<long double>);
  const long double c = cosl(angle);
  const long double s = sign * sinl(angle);
  return {static_cast<double>(c), static_cast<double>(s)};
}

} // namespace fft::mathcore

#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
