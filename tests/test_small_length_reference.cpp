// Acceptance: small-length direct-DFT reference. Every length 1..32 is
// checked against the independent long-double O(N^2) DFT with a concrete
// numeric bound and a concrete failure category assertion.
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "contract/numeric_contract.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

static std::vector<Cmplx> makeSignal(std::size_t n, long long salt) {
  std::vector<Cmplx> x(n);
  for (std::size_t i = 0; i < n; ++i) {
    const double t = static_cast<double>(i);
    x[i] = Cmplx(std::cos(0.37 * t + 0.11 * static_cast<double>(salt)),
                 std::sin(0.53 * t - 0.07 * static_cast<double>(salt)));
  }
  return x;
}

int main() {
  for (std::size_t n = 1; n <= 32; ++n) {
    tf::begin("small N=" + std::to_string(n) +
              " matches independent direct DFT (concrete bound 5e-9)");
    auto x = makeSignal(n, static_cast<long long>(n));
    auto got = mc::fft(x);
    auto refr = fft::reference::directDftDouble(x, -1);
    tf::check(got.error.status == fft::common::FftStatus::Ok,
              "forward must succeed");
    auto met = fft::contract::measureAgainstReference(got.out, refr.out);
    tf::check(met.maxAbsError <= 5e-9,
              "maxAbs=" + std::to_string(met.maxAbsError) + " > 5e-9");
    auto v = fft::contract::judge(met, {1e-8, 1e-4, 1e-2});
    tf::check(v.category == fft::common::FftStatus::Ok,
              std::string("verdict category must be Ok, got ") +
                  fft::common::statusName(v.category) + ": " +
                  v.failureReason);
  }

  // Explicit failure-category assertion: sabotage one bin and confirm the
  // contract reports ReferenceMismatch rather than silently passing.
  tf::begin("contract flags REFERENCE_MISMATCH on sabotaged spectrum");
  {
    std::size_t n = 9;
    auto x = makeSignal(n, 3);
    auto got = mc::fft(x);
    auto refr = fft::reference::directDftDouble(x, -1);
    got.out[4] += Cmplx(1.0, 0.0); // gross corruption
    auto met = fft::contract::measureAgainstReference(got.out, refr.out);
    auto v = fft::contract::judge(met, {1e-8, 1e-4, 1e-2});
    tf::check(v.category == fft::common::FftStatus::ReferenceMismatch,
              std::string("expected ReferenceMismatch, got ") +
                  fft::common::statusName(v.category));
    tf::check(!v.failureReason.empty(),
              "failure reason must be populated separately");
    tf::check(met.worstBin == 4, "worst bin must identify bin 4");
  }
  return tf::finish();
}
