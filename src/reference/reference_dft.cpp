#include "reference_dft.hpp"
#include <numbers>

namespace fft::reference {

using common::FftError;
using common::FftStatus;

namespace {
long double tau() { return 2.0L * std::numbers::pi_v<long double>; }
} // namespace

DirectDftResult directDft(const std::vector<CmplxLD>& in,
                          int sign,
                          const DirectDftOptions& options) {
  DirectDftResult r;
  if (in.empty()) {
    r.error = {FftStatus::EmptyInput, "directDft: input length is zero"};
    return r;
  }
  if (sign != 1 && sign != -1) {
    r.error = {FftStatus::InvalidArgument, "directDft: sign must be +1 or -1"};
    return r;
  }
  const auto n = in.size();
  const std::size_t budget =
      options.memoryBudgetBytes ? options.memoryBudgetBytes
                                : common::defaultMemoryBudget();
  // Work estimate: one output vector (input is caller owned).
  const std::size_t need = n * sizeof(CmplxLD);
  if (need > budget) {
    r.error = {FftStatus::AllocationFailed,
               "directDft: estimated output memory exceeds budget"};
    return r;
  }

  r.out.assign(n, CmplxLD{});
  const long double N = static_cast<long double>(n);
  const long double base = static_cast<long double>(sign) * tau() / N;
  // Direct nested sum, exactly the DFT definition. Each output bin is an
  // independent accumulation in long double.
  for (std::size_t k = 0; k < n; ++k) {
    CmplxLD acc{0.0L, 0.0L};
    for (std::size_t j = 0; j < n; ++j) {
      const long double theta =
          base * static_cast<long double>(j) * static_cast<long double>(k);
      const long double c = cosl(theta);
      const long double s = sinl(theta);
      const CmplxLD w{c, s};
      acc += in[j] * w;
    }
    if (options.normalize) acc /= N;
    r.out[k] = acc;
  }
  r.error = {FftStatus::Ok, {}};
  return r;
}

DirectDftResult directDftDouble(const std::vector<std::complex<double>>& in,
                                int sign,
                                const DirectDftOptions& options) {
  std::vector<CmplxLD> wide(in.begin(), in.end());
  return directDft(wide, sign, options);
}

} // namespace fft::reference
