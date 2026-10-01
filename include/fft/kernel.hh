// SPDX-License-Identifier: MIT
// FFT algorithm kernel: radix-2 Cooley-Tukey plus Bluestein fallback that
// supports arbitrary (non-power-of-two) complex lengths.
#pragma once

#include "fft/types.hh"

#include <complex>
#include <cstddef>
#include <vector>

namespace fft {

// Smallest power of two M with M >= need. Returns 0 on overflow.
std::size_t next_pow2(std::size_t need) noexcept;

// In-place iterative radix-2 FFT. n MUST be a power of two.
// sign = -1.0 -> forward, sign = +1.0 -> inverse sign convention.
// No 1/N scaling is applied here.
ErrorCode radix2_fft(std::vector<Complex>& a, double sign) noexcept;

// Arbitrary-length complex DFT via Bluestein's algorithm.
// out.size() must equal in.size() == n. The forward transform is unscaled;
// callers apply inverse normalization separately.
// sign = -1.0 forward / +1.0 inverse-sign.
// conv_length (optional) receives the chosen M (M >= 2n-1, power of two).
// mem (optional) receives deterministic buffer accounting.
ErrorCode bluestein(const std::vector<Complex>& in,
                    std::vector<Complex>&       out,
                    double                      sign,
                    std::size_t*                conv_length = nullptr,
                    MemoryReport*               mem         = nullptr) noexcept;

// High level facade. Runs radix-2 directly when n is a power of two, otherwise
// falls back to Bluestein. Applies 1/n normalization for inverse.
FFTResult transform(const std::vector<Complex>& in,
                    std::vector<Complex>&       out,
                    Direction                   dir,
                    const std::string&          request_id = "",
                    StepTrace*                  trace      = nullptr) noexcept;

} // namespace fft
