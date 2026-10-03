// Early vertical slice: exercises build link, radix2 + Bluestein paths and
// the independent reference oracle for a few concrete small lengths.
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;
namespace ref = fft::reference;

static void expectVsReference(std::size_t n, double tol) {
  tf::begin("fft matches direct DFT: N=" + std::to_string(n));
  std::vector<Cmplx> x(n);
  for (std::size_t i = 0; i < n; ++i)
    x[i] = Cmplx(std::cos(0.7 * i + 0.3), std::sin(1.1 * i - 0.2));
  auto got = mc::fft(x);
  tf::checkEq<int>(static_cast<int>(got.error.status), 0,
                   "fft status must be Ok");
  auto refr = ref::directDftDouble(x, -1);
  tf::checkEq<int>(static_cast<int>(refr.error.status), 0,
                   "reference status must be Ok");
  double maxErr = 0.0;
  for (std::size_t k = 0; k < n; ++k) {
    Cmplx g(got.out[k]);
    Cmplx e(static_cast<double>(refr.out[k].real()),
            static_cast<double>(refr.out[k].imag()));
    maxErr = std::max(maxErr, std::abs(g - e));
  }
  tf::check(maxErr <= tol,
            "max abs error " + std::to_string(maxErr) +
                " exceeds tol " + std::to_string(tol));

  // Round trip with fixed normalization.
  auto back = mc::ifft(got.out);
  double rt = 0.0;
  for (std::size_t i = 0; i < n; ++i) rt = std::max(rt, std::abs(back.out[i] - x[i]));
  tf::check(rt <= tol, "ifft(fft(x)) error " + std::to_string(rt));
}

int main() {
  expectVsReference(4, 1e-10);    // power of two -> radix2
  expectVsReference(1, 1e-12);    // trivial length
  expectVsReference(7, 1e-9);     // prime -> Bluestein
  return tf::finish();
}
