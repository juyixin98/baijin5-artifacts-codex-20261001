// SPDX-License-Identifier: MIT
// Standalone independent benchmark entry. Reports, per length: algorithm,
// convolution padding M, timing, residual vs an independent reference, the
// uncertainty band, and memory (deterministic accounting + process RSS).
#include "fft/benchmark.hh"
#include "fft/errors.hh"

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#ifndef FFT_VERSION
#  define FFT_VERSION "0.0.0-dev"
#endif

int main(int argc, char** argv) {
  std::string gen = argc > 1 ? argv[1] : "random";
  unsigned seed = argc > 2 ? static_cast<unsigned>(std::strtoul(argv[2], 0, 10))
                           : 12345u;
  std::size_t repeats = argc > 3 ? std::strtoull(argv[3], 0, 10) : 3;

  // Power-of-two (radix-2), prime and awkward composite (Bluestein) lengths.
  const std::vector<std::size_t> lengths = {
      64, 100, 127, 256, 977, 1024, 4093, 4096, 4099};

  std::cout << "FFT benchmark " FFT_VERSION
            << " generator=" << gen << " seed=" << seed
            << " repeats=" << repeats << "\n";
  std::cout << "N\talg\tM\tt_min_ms\tt_med_ms\tmax_abs\tmax_rel\t"
               "band\twork_kb\trss_kb\n";

  int rc = 0;
  for (std::size_t n : lengths) {
    fft::BenchConfig cfg;
    cfg.n = n;
    cfg.forward = true;
    cfg.repeats = repeats;
    cfg.generator = gen;
    cfg.seed = seed;
    cfg.request_id = "bench-" + std::to_string(n);
    const auto r = fft::run_benchmark(cfg, 1e-9, 1e-11);
    if (r.code != fft::ErrorCode::Ok) {
      fft::ErrorInfo e{r.code, "benchmark failed", "run_benchmark",
                       cfg.request_id};
      std::cout << fft::format_failure(e) << "\n";
      rc = 1;
      continue;
    }
    std::printf("%zu\t%s\t%zu\t%.4f\t\t%.4f\t\t%.3e\t%.3e\t%s\t%.1f\t%zu\n",
                n, r.algorithm.c_str(), r.convolution_length,
                r.time_ms_min, r.time_ms_med,
                r.reference_used ? r.error.max_abs_err : -1.0,
                r.reference_used ? r.error.max_rel_err : -1.0,
                r.uncertainty.band.c_str(),
                r.memory.working_set_bytes / 1024.0,
                fft::process_peak_rss_bytes() / 1024u);
    if (!r.uncertainty.acceptable) rc = 3;
  }
  std::cout << "benchmark complete rc=" << rc << "\n";
  return rc;
}
