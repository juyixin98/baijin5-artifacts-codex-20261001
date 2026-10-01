// SPDX-License-Identifier: MIT
// Forward/inverse round trip across power-of-two, prime and composite lengths.
#include "fft/kernel.hh"
#include "naive_dft.hh"
#include "test_framework.hh"

#include <random>

TST_CASE(forward_inverse_roundtrip_many_lengths) {
  std::mt19937_64 rng(20240917);
  std::uniform_real_distribution<double> uni(-1, 1);
  const std::size_t lengths[] = {1u, 2u, 3u, 4u, 8u, 13u, 16u, 63u, 64u,
                                 100u, 127u, 255u, 1000u, 1024u};
  for (std::size_t n : lengths) {
    std::vector<fft::Complex> x(n);
    for (auto& z : x) z = fft::Complex(uni(rng), uni(rng));
    std::vector<fft::Complex> X(n), back(n);
    auto rf = fft::transform(x, X, fft::Direction::Forward,
                             "rt-" + std::to_string(n));
    CHECK_EQ_CODE(rf.code, fft::ErrorCode::Ok);
    auto ri = fft::transform(X, back, fft::Direction::Inverse,
                             "rt-" + std::to_string(n));
    CHECK_EQ_CODE(ri.code, fft::ErrorCode::Ok);
    const double tol = 1e-8 * std::sqrt(double(n));
    for (std::size_t k = 0; k < n; ++k)
      CHECK_CABS_NEAR(back[k], x[k], tol);
  }
}
