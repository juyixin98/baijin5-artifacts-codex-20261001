// Acceptance: prime lengths force the Bluestein path. Checked against the
// independent direct DFT (large primes use analytic fixtures because O(N^2)
// reference is intentionally expensive).
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "bench/benchmark.hpp"
#include "contract/numeric_contract.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

int main() {
  const std::size_t primes[] = {2, 3, 5, 7, 11, 13, 17, 31, 61, 97, 127, 251};
  for (std::size_t p : primes) {
    tf::begin("prime N=" + std::to_string(p) +
              " uses Bluestein and matches direct DFT");
    std::vector<Cmplx> x(p);
    for (std::size_t i = 0; i < p; ++i)
      x[i] = Cmplx(std::cos(0.23 * i + 0.5), std::sin(0.41 * i - 0.2));
    auto got = mc::fft(x);
    tf::check(got.error.status == fft::common::FftStatus::Ok,
              "fft failed: " + got.error.message);
    if (p > 2) {
      tf::check(std::string(got.kernelPath) == "bluestein",
                std::string("expected bluestein kernel, got ") +
                    got.kernelPath);
    }
    auto refr = fft::reference::directDftDouble(x, -1);
    auto met = fft::contract::measureAgainstReference(got.out, refr.out);
    tf::check(met.maxAbsError <= 1e-8 * std::max<std::size_t>(p, 1),
              "maxAbs=" + std::to_string(met.maxAbsError));
  }

  // Large prime: analytic impulse spectrum (exact closed form).
  tf::begin("large prime N=5003 impulse matches closed-form DFT");
  {
    auto fx = fft::bench::generateFixture(
        fft::bench::FixtureKind::Impulse, 5003, 9, 1000);
    auto got = mc::fft(fx.samples);
    tf::check(std::string(got.kernelPath) == "bluestein", "must be bluestein");
    tf::check(got.convolutionLength >= 2 * 5003 - 1,
              "conv L must satisfy linear convolution");
    auto ac = fft::bench::checkAnalyticSpectrum(fx, got.out);
    tf::check(ac.passed, ac.detail);
  }
  return tf::finish();
}
