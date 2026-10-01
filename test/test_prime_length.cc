// SPDX-License-Identifier: MIT
// Prime lengths force Bluestein; compare every bin to the independent DFT.
#include "fft/kernel.hh"
#include "naive_dft.hh"
#include "test_framework.hh"

#include <random>

TST_CASE(prime_lengths_match_reference) {
  std::mt19937_64 rng(987654321u);
  std::uniform_real_distribution<double> uni(-1, 1);
  const std::size_t primes[] = {2u, 3u, 5u, 7u, 11u, 13u, 31u, 97u,
                                127u, 251u, 977u};
  for (std::size_t n : primes) {
    std::vector<fft::Complex> x(n);
    for (auto& z : x) z = fft::Complex(uni(rng), uni(rng) * 2.0);
    std::vector<fft::Complex> got(n);
    auto res = fft::transform(x, got, fft::Direction::Forward,
                              "prime-" + std::to_string(n));
    CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
    if (n > 2) CHECK(res.algorithm == "bluestein");
    const auto want = ref::dft(x);
    const double scale = 1.0;
    const double tol = 1e-8 * std::sqrt(double(n)) + 1e-10 * scale;
    for (std::size_t k = 0; k < n; ++k)
      CHECK_CABS_NEAR(got[k], want[k], tol);
  }
}
