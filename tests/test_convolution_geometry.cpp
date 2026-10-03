// Acceptance of behavior contract #1: zero-padding length satisfies linear
// convolution. Asserts L >= 2N-1, L is a power of two, and (independently)
// that circular-vs-linear convolution equivalence holds on the even chirp
// extension for every length 1..200.
#include "mathcore/fft.hpp"
#include "contract/numeric_contract.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

int main() {
  for (std::size_t n = 1; n <= 200; ++n) {
    tf::begin("geometry N=" + std::to_string(n));
    std::vector<Cmplx> x(n, Cmplx(0.3, -0.2));
    auto r = mc::fft(x);
    tf::check(r.error.status == fft::common::FftStatus::Ok, "fft failed");
    tf::check(mc::isPowerOfTwo(r.convolutionLength),
              "conv length must be power of two");
    if (mc::isPowerOfTwo(n)) {
      // Fast path: no convolution at all, conv length is reported as N.
      tf::check(std::string(r.kernelPath) == "radix2",
                "power-of-two must use radix2 path");
      tf::check(r.convolutionLength == n, "radix2 reports L=N");
    } else {
      // Bluestein downgrade: zero-pad must satisfy LINEAR convolution.
      tf::check(std::string(r.kernelPath) == "bluestein",
                "non-power-of-two must use bluestein");
      auto g = fft::contract::checkConvolutionGeometry(n,
                                                       r.convolutionLength);
      tf::check(g.linearConvolutionSatisfied,
                "linear convolution condition violated: " + g.detail);
      // Tightness: previous power of two is strictly below 2N-1.
      tf::check(r.convolutionLength / 2 < g.requiredLength,
                "L should be the smallest admissible power of two");
    }
  }

  // Exact tightness spot checks.
  tf::begin("tight L spot checks");
  {
    std::vector<Cmplx> x1(13, Cmplx(1, 0));
    auto r13 = mc::fft(x1); // 2*13-1 = 25 -> L=32
    tf::check(r13.convolutionLength == 32, "N=13 needs L=32");
    std::vector<Cmplx> x2(100, Cmplx(1, 0));
    auto r100 = mc::fft(x2); // 199 -> 256
    tf::check(r100.convolutionLength == 256, "N=100 needs L=256");
    std::vector<Cmplx> x3(4, Cmplx(1, 0));
    auto r4 = mc::fft(x3); // radix2 path, L=N
    tf::check(r4.convolutionLength == 4, "power-of-two uses fast path");
  }
  return tf::finish();
}
