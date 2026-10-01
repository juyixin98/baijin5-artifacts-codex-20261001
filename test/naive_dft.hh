// SPDX-License-Identifier: MIT
// Independent reference implementation. This is a deliberately separate,
// textbook O(N^2) DFT written only against <complex>: it never calls the
// Bluestein/radix-2 kernel under test, so reference answers are not produced
// by the code being validated.
#pragma once

#include <complex>
#include <cstddef>
#include <vector>

namespace ref {

using C = std::complex<double>;

// Forward, unscaled: X[k] = sum_n x[n] exp(-i 2pi n k / N).
inline std::vector<C> dft(const std::vector<C>& x) {
  constexpr double kPi = 3.141592653589793238462643383279502884;
  const std::size_t n = x.size();
  std::vector<C> X(n, C(0, 0));
  for (std::size_t k = 0; k < n; ++k)
    for (std::size_t j = 0; j < n; ++j) {
      const double a = -2.0 * kPi * double(k) * double(j) / double(n);
      X[k] += x[j] * C(std::cos(a), std::sin(a));
    }
  return X;
}

} // namespace ref
