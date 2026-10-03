#include "fft.hpp"
#include "chirp.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <numbers>

namespace fft::mathcore {

using common::FftError;
using common::FftStatus;

namespace {

constexpr double kPi = std::numbers::pi;
constexpr long double kPiLD = std::numbers::pi_v<long double>;

bool finiteInput(const std::vector<Cmplx>& x) noexcept {
  for (const auto& v : x) {
    if (!std::isfinite(v.real()) || !std::isfinite(v.imag())) return false;
  }
  return true;
}

// In-place radix-2 iterative Cooley-Tukey. No normalization (both forward
// and inverse use the same unnormalized transform; sign selects exponent).
void radix2InPlace(std::vector<Cmplx>& a, int sign) {
  const std::size_t n = a.size();
  // Bit-reversal permutation.
  for (std::size_t i = 1, j = 0; i < n; ++i) {
    std::size_t bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) std::swap(a[i], a[j]);
  }
  for (std::size_t len = 2; len <= n; len <<= 1) {
    const double ang = sign * 2.0 * kPi / static_cast<double>(len);
    const Cmplx wlen(std::cos(ang), std::sin(ang));
    const std::size_t half = len >> 1;
    for (std::size_t i = 0; i < n; i += len) {
      Cmplx w(1.0, 0.0);
      for (std::size_t j = 0; j < half; ++j) {
        const Cmplx u = a[i + j];
        const Cmplx v = a[i + j + half] * w;
        a[i + j] = u + v;
        a[i + j + half] = u - v;
        w *= wlen;
      }
    }
  }
}

} // namespace

bool isPowerOfTwo(std::size_t n) noexcept {
  return n != 0 && (n & (n - 1)) == 0;
}

std::size_t nextPowerOfTwo(std::size_t n) noexcept {
  if (n <= 1) return 1;
  --n;
  std::size_t p = 1;
  while (n >>= 1) p <<= 1;
  p <<= 1;
  return p;
}

FftResult transform(const std::vector<Cmplx>& x, int sign,
                    const common::FftOptions& options) {
  FftResult res;
  if (x.empty()) {
    res.error = {FftStatus::EmptyInput, "fft: input length must be >= 1"};
    return res;
  }
  if (sign != 1 && sign != -1) {
    res.error = {FftStatus::InvalidArgument,
                 "fft: sign must be -1 (forward) or +1 (inverse)"};
    return res;
  }
  if (!finiteInput(x)) {
    res.error = {FftStatus::ValueOutOfDomain,
                 "fft: input contains NaN or Inf"};
    return res;
  }

  const std::size_t N = x.size();
  const std::size_t budget = options.memoryBudgetBytes
                                 ? options.memoryBudgetBytes
                                 : common::defaultMemoryBudget();

  // Power-of-two fast path.
  if (isPowerOfTwo(N)) {
    const std::size_t need = 2 * N * sizeof(Cmplx); // input + workspace
    if (need > budget) {
      res.error = {FftStatus::AllocationFailed,
                   "fft: radix2 workspace exceeds memory budget"};
      return res;
    }
    res.out = x;
    radix2InPlace(res.out, sign);
    if (sign == 1) {
      const double inv = 1.0 / static_cast<double>(N);
      for (auto& v : res.out) v *= inv;
    }
    res.usedLength = N;
    res.convolutionLength = N;
    res.peakWorkspaceBytes = need;
    res.kernelPath = "radix2";
    res.error = {FftStatus::Ok, {}};
    return res;
  }

  // ---- Bluestein chirp-z downgrade for arbitrary N ----
  // Linear convolution support: L is a power of two with L >= 2N - 1.
  const std::size_t required = 2 * N - 1;
  const std::size_t L = nextPowerOfTwo(required);
  if (L < required) {
    res.error = {FftStatus::InvalidLength,
                 "fft: length too large (convolution size overflow)"};
    return res;
  }
  // Peak simultaneous storage: input(N) + chirp(N) + two spectra(L) + out(N).
  const std::size_t need = (2 * N + 2 * L) * sizeof(Cmplx);
  if (need > budget) {
    res.error = {FftStatus::AllocationFailed,
                 "fft: Bluestein workspace exceeds memory budget"};
    return res;
  }

  std::vector<Cmplx> chirp(N);
  for (std::size_t n = 0; n < N; ++n) chirp[n] = chirpAt(n, N, sign);

  // m[n] = chirp[n] * x[n], zero padded to L.
  std::vector<Cmplx> ma(L, Cmplx{});
  for (std::size_t n = 0; n < N; ++n) ma[n] = chirp[n] * x[n];

  // b holds the even extension of conj(chirp) so that circular convolution
  // over L equals the linear convolution m * conj(chirp).
  std::vector<Cmplx> bc(L, Cmplx{});
  bc[0] = std::conj(chirp[0]);
  for (std::size_t n = 1; n < N; ++n) {
    bc[n] = std::conj(chirp[n]);
    bc[L - n] = std::conj(chirp[n]);
  }

  radix2InPlace(ma, -1);
  radix2InPlace(bc, -1);
  for (std::size_t i = 0; i < L; ++i) ma[i] *= bc[i];
  radix2InPlace(ma, +1); // unnormalized inverse

  res.out.resize(N);
  for (std::size_t k = 0; k < N; ++k) {
    // Normalize the convolution FFT by 1/L; inverse DFT path adds 1/N.
    Cmplx conv = ma[k] / static_cast<double>(L);
    if (sign == 1) conv *= 1.0 / static_cast<double>(N);
    res.out[k] = chirp[k] * conv;
  }

  res.usedLength = N;
  res.convolutionLength = L;
  res.peakWorkspaceBytes = need;
  res.kernelPath = "bluestein";
  res.error = {FftStatus::Ok, {}};
  return res;
}

FftResult fft(const std::vector<Cmplx>& x,
              const common::FftOptions& options) {
  return transform(x, -1, options);
}

FftResult ifft(const std::vector<Cmplx>& X,
               const common::FftOptions& options) {
  return transform(X, +1, options);
}

} // namespace fft::mathcore
