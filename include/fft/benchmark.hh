// SPDX-License-Identifier: MIT
// Independent benchmark / error-reporting module. It does not implement the
// algorithm; it drives the public facade, measures time and memory, and
// reports residuals plus an explicit uncertainty verdict.
#pragma once

#include "fft/errors.hh"
#include "fft/types.hh"

#include <complex>
#include <cstddef>
#include <string>
#include <vector>

namespace fft {

struct BenchConfig {
  std::size_t n            = 0;
  bool        forward      = true;
  std::size_t repeats      = 1;
  std::string request_id;
  std::string generator;   // "random" | "impulse" | "large-dynamic"
  unsigned    seed         = 12345;
};

struct BenchReport {
  std::string  request_id;
  std::string  version;
  std::string  algorithm;
  std::size_t  n = 0;
  std::size_t  convolution_length = 0;
  double       time_ms_min = 0;
  double       time_ms_med = 0;
  ErrorCode    code = ErrorCode::Ok;
  ErrorStats   error;                 // vs independent reference when feasible
  bool         reference_used = false;
  UncertaintyVerdict uncertainty;
  MemoryReport memory;
};

// Deterministic local synthetic data generator (no external data).
std::vector<Complex> make_synthetic(std::size_t n,
                                    const std::string& kind,
                                    unsigned seed);

// Maximum resident set size in bytes from the OS (getrusage), 0 if unavailable.
std::size_t process_peak_rss_bytes();

// Compute residual statistics of got versus an independently supplied ref.
ErrorStats compute_stats(const std::vector<Complex>& got,
                         const std::vector<Complex>& ref);

// Run one benchmark. When a reference can be afforded it is evaluated once;
// otherwise uncertainty is marked untrusted and stated explicitly.
BenchReport run_benchmark(const BenchConfig& cfg,
                          double tol_abs = 1e-9,
                          double tol_rel = 1e-11);

} // namespace fft
