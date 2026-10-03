// Acceptance: fixed forward/inverse normalization.
//   forward unscaled, inverse 1/N, so ifft(fft(x))==x.
// Also asserts the inverse against the long-double reference inverse DFT.
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "contract/numeric_contract.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

int main() {
  for (std::size_t n : {1u, 2u, 3u, 8u, 9u, 13u, 64u, 100u, 257u}) {
    tf::begin("normalization N=" + std::to_string(n));
    std::vector<Cmplx> x(n);
    for (std::size_t i = 0; i < n; ++i)
      x[i] = Cmplx(std::cos(0.61 * i + 0.2), std::sin(0.33 * i));

    auto X = mc::fft(x);
    auto back = mc::ifft(X.out);
    double rt = fft::contract::roundTripError(x, back.out);
    tf::check(rt <= 1e-9 * std::max<std::size_t>(n, 1),
              "round trip error " + std::to_string(rt));

    // Independent inverse reference, normalized by the SAME 1/N contract.
    fft::reference::DirectDftOptions ro;
    ro.normalize = true;
    auto refInv = fft::reference::directDftDouble(X.out, +1, ro);
    double e = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
      Cmplx r(static_cast<double>(refInv.out[i].real()),
              static_cast<double>(refInv.out[i].imag()));
      e = std::max(e, std::abs(back.out[i] - r));
    }
    tf::check(e <= 1e-8 * std::max<std::size_t>(n, 1),
              "inverse vs reference " + std::to_string(e));
  }

  // Orthogonality constant: fft(ones)[0] == N and others 0 (unscaled).
  tf::begin("forward is unscaled: DFT(ones)=N at DC");
  {
    std::size_t n = 17;
    std::vector<Cmplx> ones(n, Cmplx(1, 0));
    auto X = mc::fft(ones);
    tf::checkCloseAbs(X.out[0], Cmplx(n, 0), 1e-9, "DC must be N");
    for (std::size_t k = 1; k < n; ++k)
      tf::check(std::abs(X.out[k]) < 1e-9, "non-DC bin must be zero");
  }
  return tf::finish();
}
