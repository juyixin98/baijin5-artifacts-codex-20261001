// SPDX-License-Identifier: MIT
// Minimal vertical slice: Bluestein on a small non-power-of-two length must
// match the independent O(N^2) reference.
#include "fft/kernel.hh"
#include "naive_dft.hh"
#include "test_framework.hh"

#include <complex>
#include <vector>

TST_CASE(small_length_matches_independent_dft) {
  using fft::Complex;
  const std::size_t n = 5; // non-power-of-two -> forces Bluestein
  std::vector<Complex> x = {{1, 0}, {2, -1}, {0, 3}, {-1, -2}, {0.5, 0.5}};

  std::vector<Complex> got(n);
  auto res = fft::transform(x, got, fft::Direction::Forward, "slice-001");
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
  CHECK(res.algorithm == "bluestein");
  CHECK(res.convolution_length >= 2 * n - 1); // linear convolution padding

  const auto want = ref::dft(x); // reference not produced by kernel
  const double tol = 1e-10;
  for (std::size_t k = 0; k < n; ++k)
    CHECK_CABS_NEAR(got[k], want[k], tol);
}
