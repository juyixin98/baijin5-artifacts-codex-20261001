// SPDX-License-Identifier: MIT
// Large dynamic-range input: validate scaled tolerances against the
// independent reference. Mixes 1e8-scale, unit and 1e-7-scale samples.
#include "fft/benchmark.hh"
#include "fft/kernel.hh"
#include "naive_dft.hh"
#include "test_framework.hh"

#include <algorithm>
#include <vector>

TST_CASE(large_dynamic_range_matches_reference) {
  const std::size_t n = 503; // prime -> Bluestein
  const auto x = fft::make_synthetic(n, "large-dynamic", 424242u);

  std::vector<fft::Complex> got(n);
  auto res = fft::transform(x, got, fft::Direction::Forward, "dyn-503");
  CHECK_EQ_CODE(res.code, fft::ErrorCode::Ok);

  const auto want = ref::dft(x);
  fft::ErrorStats st = fft::compute_stats(got, want);
  // Relative tolerance scales with the signal; absolute floor catches the
  // small components without swamping them by the 1e8 bins.
  const double tol = 1e-8 * std::max(1.0, st.ref_scale) + 1e-4;
  CHECK(st.max_abs_err < tol);
  CHECK(st.max_rel_err < 1e-8);
}
