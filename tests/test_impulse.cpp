// Acceptance: impulse input at various positions/lengths. The DFT has a
// closed form |X[k]|=1 with specific phase; we assert concrete bins.
#include "mathcore/fft.hpp"
#include "bench/benchmark.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

int main() {
  const std::size_t lengths[] = {1, 8, 9, 16, 100, 257, 1000};
  for (std::size_t n : lengths) {
    for (std::size_t pos : {std::size_t(0), n / 3, n - 1}) {
      tf::begin("impulse N=" + std::to_string(n) + " pos=" +
                std::to_string(pos));
      auto fx = fft::bench::generateFixture(
          fft::bench::FixtureKind::Impulse, n, 1, pos);
      auto got = mc::fft(fx.samples);
      tf::check(got.error.status == fft::common::FftStatus::Ok,
                "fft failed");
      auto ac = fft::bench::checkAnalyticSpectrum(fx, got.out);
      tf::check(ac.passed, ac.detail);
      // Explicit DC bin: X[0] = sum x = 1.
      tf::checkCloseAbs(got.out[0], Cmplx(1.0, 0.0),
                        1e-9 * std::max<double>(n, 1), "DC bin must equal 1");
    }
  }
  return tf::finish();
}
