#pragma once
// Complex FFT public contract for arbitrary-length inputs.
//
// Fixed normalization contract:
//   fft (forward): X[k] = sum_{n=0}^{N-1} x[n] exp(-i 2pi n k / N)
//   ifft(inverse): x[n] = (1/N) sum_{k=0}^{N-1} X[k] exp(+i 2pi n k / N)
// so that ifft(fft(x)) == x.
//
// Implementation strategy ("Bluestein downgrade"):
//   * power-of-two N  -> in-place radix-2 Cooley-Tukey
//   * arbitrary N     -> Bluestein chirp-z rewrite, whose convolution is
//                        evaluated by a radix-2 FFT of length L >= 2N-1
//                        (L a power of two), satisfying linear convolution.
#include "common/status.hpp"
#include <complex>
#include <cstddef>
#include <vector>

namespace fft::mathcore {

using Cmplx = std::complex<double>;

struct FftResult {
  common::FftError error;
  std::vector<Cmplx> out;
  // Diagnostics describing where the work happened.
  std::size_t usedLength = 0;          // logical transform length N
  std::size_t convolutionLength = 0;   // L (== N for power-of-two fast path)
  std::size_t peakWorkspaceBytes = 0; // input + all internal buffers
  const char* kernelPath = "";         // "radix2" or "bluestein"
};

// Forward (unscaled) DFT.
FftResult fft(const std::vector<Cmplx>& x,
              const common::FftOptions& options = {});

// Inverse DFT with fixed 1/N normalization.
FftResult ifft(const std::vector<Cmplx>& X,
               const common::FftOptions& options = {});

// Generic transform. sign = -1 forward, +1 inverse (inverse gets 1/N scale).
FftResult transform(const std::vector<Cmplx>& x, int sign,
                    const common::FftOptions& options = {});

// Predicate: radix-2 fast path is available for this length.
bool isPowerOfTwo(std::size_t n) noexcept;

// Smallest power of two >= n (returns 0 on overflow).
std::size_t nextPowerOfTwo(std::size_t n) noexcept;

} // namespace fft::mathcore
