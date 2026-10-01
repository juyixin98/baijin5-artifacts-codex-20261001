// SPDX-License-Identifier: MIT
#include "fft/kernel.hh"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <new>
#include <string>

#ifndef FFT_VERSION
#  define FFT_VERSION "0.0.0-dev"
#endif

namespace fft {

namespace {
constexpr double kPi = 3.141592653589793238462643383279502884;

bool is_pow2(std::size_t n) noexcept { return n != 0 && (n & (n - 1)) == 0; }

bool all_finite(const std::vector<Complex>& v) noexcept {
  for (const auto& z : v) {
    if (!std::isfinite(z.real()) || !std::isfinite(z.imag())) return false;
  }
  return true;
}
} // namespace

std::size_t next_pow2(std::size_t need) noexcept {
  if (need == 0) return 1;
  if (need > (std::size_t(1) << (sizeof(std::size_t) * 8 - 1))) return 0;
  std::size_t p = 1;
  while (p < need) {
    if (p > (std::numeric_limits<std::size_t>::max() >> 1)) return 0;
    p <<= 1;
  }
  return p;
}

ErrorCode radix2_fft(std::vector<Complex>& a, double sign) noexcept {
  const std::size_t n = a.size();
  if (n == 0) return ErrorCode::EmptyLength;
  if (!is_pow2(n)) return ErrorCode::UnsupportedLength;

  // Iterative bit-reversal permutation.
  for (std::size_t i = 1, j = 0; i < n; ++i) {
    std::size_t bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) std::swap(a[i], a[j]);
  }

  // Butterflies with explicit twiddle factors; no recursion, extra buffers.
  for (std::size_t len = 2; len <= n; len <<= 1) {
    const double ang = sign * 2.0 * kPi / static_cast<double>(len);
    const Complex wlen(std::cos(ang), std::sin(ang));
    for (std::size_t i = 0; i < n; i += len) {
      Complex w(1.0, 0.0);
      for (std::size_t j = 0; j < len / 2; ++j) {
        const Complex u = a[i + j];
        const Complex v = a[i + j + len / 2] * w;
        a[i + j]             = u + v;
        a[i + j + len / 2]   = u - v;
        w *= wlen;
      }
    }
  }

  for (const auto& z : a)
    if (!std::isfinite(z.real()) || !std::isfinite(z.imag()))
      return ErrorCode::InternalError;
  return ErrorCode::Ok;
}

ErrorCode bluestein(const std::vector<Complex>& in,
                    std::vector<Complex>&       out,
                    double                      sign,
                    std::size_t*                conv_length,
                    MemoryReport*               mem) noexcept {
  const std::size_t n = in.size();
  if (n == 0) return ErrorCode::EmptyLength;
  if (out.size() != n) return ErrorCode::UnsupportedLength;
  if (!all_finite(in)) return ErrorCode::NaNOrInfInput;

  // (1) Convolution zero-padding: need M >= 2n-1 for an *exact* linear
  //     convolution. Choose the next power of two for the radix-2 engine.
  const std::size_t need = 2 * n - 1;
  if (need < n) return ErrorCode::LengthOverflow;
  const std::size_t m = next_pow2(need);
  if (m == 0 || m < need) return ErrorCode::LengthOverflow;

  std::vector<Complex> a, b;
  try {
    a.assign(m, Complex(0, 0));
    b.assign(m, Complex(0, 0));
  } catch (const std::bad_alloc&) {
    return ErrorCode::AllocationFailed;
  }

  // (2) Chirp phase with controlled large-index error.
  //     theta_j = pi * j^2 / n. j^2 is reduced modulo 2n using only 64-bit
  //     integer arithmetic, so the argument to sin/cos never grows with n
  //     and stays inside [-2pi, 2pi]. 2n*n <= 2^63 for every feasible n.
  const std::int64_t n64 = static_cast<std::int64_t>(n);
  const std::int64_t mod = 2 * n64;
  std::vector<double> cosc(n), sinc(n);
  try {
    cosc.resize(n);
    sinc.resize(n);
  } catch (const std::bad_alloc&) {
    return ErrorCode::AllocationFailed;
  }

  std::int64_t r = 1; // (j+1)^2 - j^2 = 2j+1, starting j=0 -> 1
  std::int64_t rr = 0; // j^2 mod 2n for current j
  for (std::int64_t j = 0; j < n64; ++j) {
    const double phase = kPi * static_cast<double>(rr) / static_cast<double>(n);
    cosc[j] = std::cos(phase);
    sinc[j] = std::sin(phase);
    rr = (rr + r) % mod;
    r += 2;
  }

  // A[j] = x[j] * w(j). B holds the Hermitian chirp filter conj(w(q)) for
  // both positive lags q=0..n-1 and the wrapped negative lags -(n-1)..-1
  // (the chirp is even, conj(w(-q)) == conj(w(q))). The gap in the middle
  // stays zero so the circular convolution equals the linear one for the
  // required lags.
  for (std::size_t j = 0; j < n; ++j) {
    const Complex wj(cosc[j], sign * sinc[j]);      // e^{-i*sign*pi j^2/n}
    const Complex wjinv(cosc[j], -sign * sinc[j]);  // e^{+i*sign*pi j^2/n}
    a[j] = in[j] * wj;
    b[j] = wjinv;
    if (j > 0) b[m - j] = wjinv;
  }

  ErrorCode ec = radix2_fft(a, -1.0);
  if (ec != ErrorCode::Ok) return ec;
  ec = radix2_fft(b, -1.0);
  if (ec != ErrorCode::Ok) return ec;

  for (std::size_t k = 0; k < m; ++k) a[k] *= b[k];

  ec = radix2_fft(a, +1.0); // unnormalized inverse sign
  if (ec != ErrorCode::Ok) return ec;

  const double inv_m = 1.0 / static_cast<double>(m);
  for (std::size_t k = 0; k < n; ++k) {
    const Complex c = a[k] * inv_m;         // linear convolution sample C[k]
    const Complex wk(cosc[k], sign * sinc[k]);
    out[k] = wk * c;
    if (!std::isfinite(out[k].real()) || !std::isfinite(out[k].imag()))
      return ErrorCode::InternalError;
  }

  if (conv_length) *conv_length = m;
  if (mem) {
    const std::size_t cplx = sizeof(Complex);
    mem->input_bytes       = n * cplx;
    mem->output_bytes      = n * cplx;
    mem->working_set_bytes = 2 * m * cplx + 2 * n * sizeof(double);
    mem->peak_total_bytes  = mem->input_bytes + mem->output_bytes +
                             mem->working_set_bytes;
  }
  return ErrorCode::Ok;
}

FFTResult transform(const std::vector<Complex>& in,
                    std::vector<Complex>&       out,
                    Direction                   dir,
                    const std::string&          request_id_in,
                    StepTrace*                  trace) noexcept {
  const std::string rid = request_id_in.empty() ? make_request_id()
                                                : request_id_in;
  FFTResult res;
  const std::size_t n = in.size();

  auto log = [&](const std::string& s) {
    if (trace) {
      trace->request_id = rid;
      trace->version    = FFT_VERSION;
      trace->location   = "fft::transform";
      trace->steps.push_back(s);
    }
  };

  if (n == 0) {
    res.code    = ErrorCode::EmptyLength;
    res.message = "input length must be non-zero";
    log("FAIL " + std::string(error_name(res.code)) + ": " + res.message);
    return res;
  }
  if (out.size() != n) {
    res.code    = ErrorCode::UnsupportedLength;
    res.message = "output buffer length must equal input length";
    log("FAIL " + std::string(error_name(res.code)) + ": " + res.message);
    return res;
  }
  if (!all_finite(in)) {
    res.code    = ErrorCode::NaNOrInfInput;
    res.message = "input contains NaN or Inf";
    log("FAIL " + std::string(error_name(res.code)) + ": " + res.message);
    return res;
  }

  const double sign = (dir == Direction::Forward) ? -1.0 : +1.0;

  log("request=" + rid + " version=" FFT_VERSION + " N=" +
      std::to_string(n) +
      " direction=" + (dir == Direction::Forward ? "forward" : "inverse"));

  if (is_pow2(n)) {
    res.algorithm = "radix2";
    res.convolution_length = n;
    log("dispatch: power-of-two -> radix-2 Cooley-Tukey");
    out = in;
    const ErrorCode ec = radix2_fft(out, sign);
    if (ec != ErrorCode::Ok) {
      res.code = ec;
      res.message = "radix-2 kernel failed";
      log("FAIL " + std::string(error_name(ec)));
      return res;
    }
    const std::size_t cplx = sizeof(Complex);
    res.memory = {n * cplx, n * cplx, n * cplx, 3 * n * cplx};
  } else {
    res.algorithm = "bluestein";
    log("dispatch: non-power-of-two -> Bluestein fallback");
    const ErrorCode ec =
        bluestein(in, out, sign, &res.convolution_length, &res.memory);
    if (ec != ErrorCode::Ok) {
      res.code = ec;
      res.message = "Bluestein kernel failed";
      log("FAIL " + std::string(error_name(ec)));
      return res;
    }
    log("convolution length M=" + std::to_string(res.convolution_length) +
        " (>= 2N-1=" + std::to_string(2 * n - 1) + ")");
  }

  // Fixed inverse normalization: exactly 1/N on every inverse output.
  if (dir == Direction::Inverse) {
    const double inv_n = 1.0 / static_cast<double>(n);
    for (auto& z : out) z *= inv_n;
    log("normalization: inverse scaled by 1/N");
  } else {
    log("normalization: forward unscaled");
  }

  log("OK working_set_bytes=" +
      std::to_string(res.memory.working_set_bytes));
  res.code    = ErrorCode::Ok;
  res.message = "ok";
  return res;
}

} // namespace fft
