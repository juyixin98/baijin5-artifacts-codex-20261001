#pragma once
// Independent reference: direct O(N^2) DFT evaluated in long double.
//
// This module is deliberately self-contained: it never calls into
// mathcore, never uses a chirp or radix kernel, and computes the DFT
// directly from its mathematical definition. It is the independent
// oracle used to accept/reject the Bluestein core.
#include "common/status.hpp"
#include <complex>
#include <vector>

namespace fft::reference {

using CmplxLD = std::complex<long double>;

// Direction: sign of the exponent. Forward uses -1, inverse uses +1.
struct DirectDftOptions {
  bool normalize = false;   // divide by N (use true for an inverse DFT)
  std::size_t memoryBudgetBytes = 0;
};

struct DirectDftResult {
  common::FftError error;
  std::vector<CmplxLD> out;
};

// Direct DFT by definition. Input is taken at long double precision.
// Max practical N is kept small by callers (O(N^2) cost).
DirectDftResult directDft(const std::vector<CmplxLD>& in,
                          int sign,
                          const DirectDftOptions& options = {});

// Convenience overload accepting double complex data.
DirectDftResult directDftDouble(const std::vector<std::complex<double>>& in,
                                int sign,
                                const DirectDftOptions& options = {});

} // namespace fft::reference
