// SPDX-License-Identifier: MIT
// Contract checks: convolution padding satisfies linear convolution,
// forward is unscaled, inverse carries exactly 1/N, version/request traced.
#include "fft/kernel.hh"
#include "naive_dft.hh"
#include "test_framework.hh"

#include <vector>

TST_CASE(padding_satisfies_linear_convolution) {
  for (std::size_t n : {1u, 2u, 3u, 5u, 7u, 31u, 100u, 977u, 4093u}) {
    std::vector<fft::Complex> x(n, fft::Complex(1, 0));
    std::vector<fft::Complex> y(n);
    fft::StepTrace tr;
    auto res = fft::transform(x, y, fft::Direction::Forward,
                              "pad-" + std::to_string(n), &tr);
    CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
    if (n > 1 && (n & (n - 1)) != 0) {
      CHECK(res.algorithm == "bluestein");
      const bool pow2 = (res.convolution_length &
                         (res.convolution_length - 1)) == 0;
      CHECK(pow2);
      CHECK(res.convolution_length >= 2 * n - 1);
    }
  }
}

TST_CASE(forward_unscaled_inverse_one_over_n) {
  using fft::Complex;
  const std::size_t n = 6;
  std::vector<Complex> x = {{1, 0}, {2, 1}, {0, -1}, {3, 0}, {-2, 2}, {0, 0}};
  std::vector<Complex> X(n), back(n);

  auto rf = fft::transform(x, X, fft::Direction::Forward, "norm-f");
  CHECK_EQ_CODE(rf.code, fft::ErrorCode::Ok);

  const auto want = ref::dft(x); // independent: forward unscaled
  for (std::size_t k = 0; k < n; ++k)
    CHECK_CABS_NEAR(X[k], want[k], 1e-10);

  // Manual 1/N check: IDFT(X) via forward-based rescaling must equal x.
  auto ri = fft::transform(X, back, fft::Direction::Inverse, "norm-i");
  CHECK_EQ_CODE(ri.code, fft::ErrorCode::Ok);
  for (std::size_t k = 0; k < n; ++k)
    CHECK_CABS_NEAR(back[k], x[k], 1e-9);
}

TST_CASE(trace_carries_request_id_and_version) {
  const std::size_t n = 3;
  std::vector<fft::Complex> x(n, fft::Complex(1, 0)), y(n);
  fft::StepTrace tr;
  auto res = fft::transform(x, y, fft::Direction::Forward, "rid-777", &tr);
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);
  CHECK(tr.request_id == "rid-777");
  CHECK(!tr.version.empty());
  CHECK(tr.location == "fft::transform");
  CHECK(!tr.steps.empty());
}
