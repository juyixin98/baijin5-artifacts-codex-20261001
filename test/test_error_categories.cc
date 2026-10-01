// SPDX-License-Identifier: MIT
// Failure-category assertions: each invalid input must produce the specific
// structured ErrorCode, and the explainer must identify it.
#include "fft/errors.hh"
#include "fft/kernel.hh"
#include "test_framework.hh"

#include <limits>
#include <string>

TST_CASE(empty_length_is_empty_length) {
  std::vector<fft::Complex> x;
  std::vector<fft::Complex> y;
  auto r = fft::transform(x, y, fft::Direction::Forward, "err-empty");
  CHECK_EQ_CODE(r.code, fft::ErrorCode::EmptyLength);
  fft::ErrorInfo e{r.code, r.message, "fft::transform", "err-empty"};
  const std::string s = fft::format_failure(e);
  CHECK(s.find("EMPTY_LENGTH") != std::string::npos);
  CHECK(s.find("err-empty") != std::string::npos);
}

TST_CASE(size_mismatch_is_unsupported_length) {
  std::vector<fft::Complex> x(4, fft::Complex(1, 0));
  std::vector<fft::Complex> y(3);
  auto r = fft::transform(x, y, fft::Direction::Forward, "err-size");
  CHECK_EQ_CODE(r.code, fft::ErrorCode::UnsupportedLength);
}

TST_CASE(nan_input_is_nan_or_inf) {
  const double inf = std::numeric_limits<double>::infinity();
  std::vector<fft::Complex> x = {{1, 0}, {2, 0}, {std::nan(""), 0}};
  std::vector<fft::Complex> y(3);
  auto r1 = fft::transform(x, y, fft::Direction::Forward, "err-nan");
  CHECK_EQ_CODE(r1.code, fft::ErrorCode::NaNOrInfInput);
  (void)inf;
  x[1] = fft::Complex(inf, 0);
  x[2] = fft::Complex(1, 0);
  auto r2 = fft::transform(x, y, fft::Direction::Forward, "err-inf");
  CHECK_EQ_CODE(r2.code, fft::ErrorCode::NaNOrInfInput);
}

TST_CASE(radix2_rejects_non_pow2) {
  std::vector<fft::Complex> a(3, fft::Complex(1, 0));
  CHECK_EQ_CODE(fft::radix2_fft(a, -1.0),
                fft::ErrorCode::UnsupportedLength);
}

TST_CASE(uncertainty_distinguished_from_failure) {
  fft::ErrorStats st;
  st.max_abs_err = 1e-12;
  st.ref_scale = 1.0;
  st.reference_available = true;
  auto v = fft::interpret_uncertainty(st, 1e-9, 1e-11);
  CHECK(v.acceptable);
  CHECK(v.band == "roundoff");

  fft::ErrorStats no_ref;
  no_ref.reference_available = false;
  auto v2 = fft::interpret_uncertainty(no_ref, 1e-9, 1e-11);
  CHECK(!v2.acceptable);
  CHECK(v2.band == "untrusted");
}
