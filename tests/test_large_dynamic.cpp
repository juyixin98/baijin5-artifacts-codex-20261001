// Acceptance: large dynamic range input. 1 + 1e-12 tone. After the FFT the
// impulse floor and the tone bin must both be recovered at their scales.
#include "mathcore/fft.hpp"
#include "bench/benchmark.hpp"
#include "reference/reference_dft.hpp"
#include "test_framework.hpp"
#include <algorithm>
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

static std::vector<Cmplx> dynamicSignal(std::size_t n,
                                        std::size_t strongBin,
                                        std::size_t weakBin,
                                        double tiny) {
  std::vector<Cmplx> x(n, Cmplx{});
  for (std::size_t i = 0; i < n; ++i) {
    double as_ = 2.0 * std::numbers::pi * strongBin * i / n;
    double aw = 2.0 * std::numbers::pi * weakBin * i / n;
    x[i] = Cmplx(std::cos(as_), std::sin(as_)) +
           tiny * Cmplx(std::cos(aw), std::sin(aw));
  }
  return x;
}

int main() {
  // Small sizes: compare against long-double reference directly.
  for (std::size_t n : {16u, 31u, 64u, 127u}) {
    tf::begin("large dynamic N=" + std::to_string(n) +
              " matches long-double reference");
    double tiny = 1e-12;
    std::size_t strong = (3 + n / 2) % n;
    auto x = dynamicSignal(n, strong, 3, tiny);
    auto got = mc::fft(x);
    auto refr = fft::reference::directDftDouble(x, -1);
    double e = 0.0;
    for (std::size_t k = 0; k < n; ++k) {
      Cmplx r(static_cast<double>(refr.out[k].real()),
              static_cast<double>(refr.out[k].imag()));
      e = std::max(e, std::abs(got.out[k] - r));
    }
    // Tone bin magnitude should be N*tiny; error must stay below half of it.
    double toneFloor = n * tiny;
    tf::check(e < 0.5 * toneFloor,
              "error " + std::to_string(e) +
                  " obscures tone floor " + std::to_string(toneFloor));
  }

  // Larger prime size: analytic tone detection at the actual bin.
  tf::begin("large dynamic N=1009 resolves 1e-12 tone at bin 7");
  {
    std::size_t n = 1009, strong = 200, weak = 7;
    auto x = dynamicSignal(n, strong, weak, 1e-12);
    auto got = mc::fft(x);
    double weakMag = std::abs(got.out[weak]);
    double expectedWeak = n * 1e-12;
    tf::check(std::abs(weakMag - expectedWeak) < 0.25 * expectedWeak,
              "weak tone magnitude " + std::to_string(weakMag) +
                  " differs from " + std::to_string(expectedWeak));
    double strongMag = std::abs(got.out[strong]);
    tf::check(std::abs(strongMag - n) < 1e-7 * n,
              "strong tone magnitude " + std::to_string(strongMag));
  }
  return tf::finish();
}
