// SPDX-License-Identifier: MIT
// Impulse at large index: analytic DFT exercises chirp phase at large j,k.
// A shifted impulse must give a pure exponential with modulus 1.
#include "fft/kernel.hh"
#include "test_framework.hh"

#include <cmath>
#include <vector>

TST_CASE(shifted_impulse_at_large_index_is_pure_exponential) {
  constexpr double kPi = 3.14159265358979323846;
  const std::size_t n = 200003; // large prime; n^2 is big, phase must be safe
  std::vector<fft::Complex> x(n, fft::Complex(0, 0));
  const std::size_t p = n - 1;
  x[p] = fft::Complex(1.0, 0.0);

  std::vector<fft::Complex> X(n);
  auto res = fft::transform(x, X, fft::Direction::Forward, "impulse-big");
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
  CHECK(res.algorithm == "bluestein");
  CHECK(res.convolution_length >= 2 * n - 1);

  double worst = 0.0;
  for (std::size_t k = 0; k < n; ++k) {
    const double a = -2.0 * kPi * double(k) * double(p) / double(n);
    const fft::Complex want(std::cos(a), std::sin(a));
    worst = std::max(worst, std::abs(X[k] - want));
  }
  CHECK(worst < 1e-7);
}

TST_CASE(unity_impulse_dc_is_constant) {
  const std::size_t n = 123457;
  std::vector<fft::Complex> x(n, fft::Complex(0, 0));
  x[0] = fft::Complex(1, 0);
  std::vector<fft::Complex> X(n);
  auto res = fft::transform(x, X, fft::Direction::Forward, "impulse-dc");
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
  double worst = 0.0;
  for (std::size_t k = 0; k < n; ++k)
    worst = std::max(worst, std::abs(X[k] - fft::Complex(1, 0)));
  CHECK(worst < 1e-10);
}
