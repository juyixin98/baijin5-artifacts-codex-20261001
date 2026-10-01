// SPDX-License-Identifier: MIT
#include "fft/benchmark.hh"
#include "fft/kernel.hh"

#ifndef FFT_VERSION
#  define FFT_VERSION "0.0.0-dev"
#endif

// Independent third-party cross-check (separate implementation family).
#include <Eigen/FFT>

#include <algorithm>
#include <cmath>
#include <random>
#include <sys/resource.h>

namespace fft {

std::vector<Complex> make_synthetic(std::size_t n,
                                    const std::string& kind,
                                    unsigned seed) {
  std::vector<Complex> x(n, Complex(0, 0));
  std::mt19937_64 rng(seed);
  std::uniform_real_distribution<double> uni(-1.0, 1.0);

  if (kind == "impulse") {
    x[0] = Complex(1.0, 0.0);
    if (n > 1) x[n / 2] = Complex(0.5, -0.5);
  } else if (kind == "large-dynamic") {
    for (std::size_t j = 0; j < n; ++j) {
      const double big = (j % 7 == 0) ? 1e8 : 1.0;
      const double tiny = (j % 13 == 0) ? 1e-7 : 0.0;
      x[j] = Complex(big + tiny, uni(rng) * 1e-6);
    }
  } else { // random
    for (auto& z : x) z = Complex(uni(rng), uni(rng));
  }
  return x;
}

std::size_t process_peak_rss_bytes() {
  struct rusage ru {};
  if (getrusage(RUSAGE_SELF, &ru) != 0) return 0;
  return static_cast<std::size_t>(ru.ru_maxrss) * 1024u; // Linux: KiB -> B
}

ErrorStats compute_stats(const std::vector<Complex>& got,
                         const std::vector<Complex>& ref) {
  ErrorStats st;
  double acc = 0.0;
  double scale = 0.0;
  for (std::size_t k = 0; k < got.size(); ++k) {
    const double d = std::abs(got[k] - ref[k]);
    const double m = std::abs(ref[k]);
    st.max_abs_err = std::max(st.max_abs_err, d);
    st.max_rel_err = std::max(st.max_rel_err,
                              m > 0.0 ? d / m : d);
    acc += d * d;
    scale = std::max(scale, m);
  }
  st.rms_err = std::sqrt(acc / static_cast<double>(got.size()));
  st.ref_scale = scale;
  st.reference_available = true;
  return st;
}

namespace {

// Independent third-party reference (Eigen). Its result is never derived from
// the Bluestein kernel under test.
std::vector<Complex> eigen_reference(const std::vector<Complex>& x,
                                     bool forward) {
  Eigen::FFT<double> fft;
  std::vector<Complex> out;
  if (forward)
    fft.fwd(out, x);
  else
    fft.inv(out, x); // Eigen inverse includes 1/N normalization
  return out;
}

double now_ms() {
  struct timespec ts {};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}
} // namespace

BenchReport run_benchmark(const BenchConfig& cfg,
                          double tol_abs,
                          double tol_rel) {
  BenchReport rep;
  rep.request_id = cfg.request_id.empty() ? make_request_id()
                                          : cfg.request_id;
  rep.version    = FFT_VERSION;
  rep.n          = cfg.n;

  const auto x = make_synthetic(cfg.n, cfg.generator, cfg.seed);
  const Direction dir = cfg.forward ? Direction::Forward
                                    : Direction::Inverse;

  std::vector<Complex> y(cfg.n);
  std::vector<double> times;
  times.reserve(cfg.repeats);

  for (std::size_t r = 0; r < cfg.repeats; ++r) {
    std::fill(y.begin(), y.end(), Complex(0, 0));
    const double t0 = now_ms();
    StepTrace trace;
    auto res = transform(x, y, dir, rep.request_id, &trace);
    const double t1 = now_ms();
    times.push_back(t1 - t0);
    rep.code               = res.code;
    rep.algorithm          = res.algorithm;
    rep.convolution_length = res.convolution_length;
    rep.memory             = res.memory;
    if (res.code != ErrorCode::Ok) {
      rep.uncertainty = {false, "untrusted",
                         "transform returned an error code"};
      return rep;
    }
  }

  std::sort(times.begin(), times.end());
  rep.time_ms_min = times.front();
  rep.time_ms_med = times[times.size() / 2];

  // Independent reference. For impulse the analytic answer is exact at any
  // size; otherwise use the third-party Eigen implementation.
  if (cfg.generator == "impulse" && cfg.forward) {
    // DFT of [1, ..., 0.5-0.5i at n/2] is analytic:
    std::vector<Complex> ref(cfg.n);
    for (std::size_t k = 0; k < cfg.n; ++k) {
      const double a = -2.0 * M_PI * double(k) * double(cfg.n / 2) /
                       double(cfg.n);
      ref[k] = Complex(1, 0) +
               Complex(0.5, -0.5) * Complex(std::cos(a), std::sin(a));
    }
    rep.reference_used = true;
    rep.error = compute_stats(y, ref);
    // Eigen is itself a fast FFT implementation (not an O(N^2) DFT), so it
    // remains an affordable independent cross-check at larger sizes.
  } else if (cfg.n <= 262144) {
    const auto ref = eigen_reference(x, cfg.forward);
    rep.reference_used = true;
    rep.error = compute_stats(y, ref);
  } else {
    rep.reference_used = false;
    rep.error.reference_available = false;
  }

  rep.uncertainty = interpret_uncertainty(rep.error, tol_abs, tol_rel);
  return rep;
}

} // namespace fft
