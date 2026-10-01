// SPDX-License-Identifier: MIT
// Independent third-party cross-check against Eigen::FFT (separate codebase
// from the kernel under test), over multiple awkward lengths.
#include <Eigen/FFT>

#include "fft/kernel.hh"
#include "test_framework.hh"

#include <random>
#include <vector>

TST_CASE(bluestein_matches_eigen_for_awkward_lengths) {
  std::mt19937_64 rng(55555u);
  std::uniform_real_distribution<double> uni(-1, 1);
  const std::size_t lengths[] = {3u, 9u, 15u, 100u, 333u, 1001u, 2047u, 4096u};
  for (std::size_t n : lengths) {
    std::vector<fft::Complex> x(n);
    for (auto& z : x) z = fft::Complex(uni(rng), uni(rng));

    std::vector<fft::Complex> got(n);
    auto res = fft::transform(x, got, fft::Direction::Forward,
                              "eig-" + std::to_string(n));
    CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);

    Eigen::FFT<double> ef;
    std::vector<std::complex<double>> ref;
    ef.fwd(ref, x); // independent implementation

    const double tol = 1e-7 * std::sqrt(double(n));
    for (std::size_t k = 0; k < n; ++k)
      CHECK_CABS_NEAR(got[k], ref[k], tol);
  }
}

TST_CASE(inverse_matches_eigen_inverse) {
  const std::size_t n = 797;
  std::mt19937_64 rng(8080u);
  std::uniform_real_distribution<double> uni(-1, 1);
  std::vector<fft::Complex> X(n);
  for (auto& z : X) z = fft::Complex(uni(rng), uni(rng));

  std::vector<fft::Complex> got(n);
  auto res = fft::transform(X, got, fft::Direction::Inverse, "eig-inv");
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);

  Eigen::FFT<double> ef;
  std::vector<std::complex<double>> ref;
  ef.inv(ref, X); // includes 1/N, matching our fixed convention
  for (std::size_t k = 0; k < n; ++k)
    CHECK_CABS_NEAR(got[k], ref[k], 1e-9);
}
