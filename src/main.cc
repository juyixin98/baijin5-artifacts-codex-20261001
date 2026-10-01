// SPDX-License-Identifier: MIT
// Runnable service entry point: a local CLI driver for the FFT backend.
//
//   fft_cli --length 977 --direction forward --generator impulse \
//            --request-id req-42 --trace
//
// Output is key/value lines (plus an optional JSON-ish result block). Failures
// and uncertainty are emitted on dedicated lines with their categories.
#include "fft/benchmark.hh"
#include "fft/errors.hh"
#include "fft/kernel.hh"

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <string>

#ifndef FFT_VERSION
#  define FFT_VERSION "0.0.0-dev"
#endif

namespace {
using fft::Complex;

std::map<std::string, std::string> parse(int argc, char** argv) {
  std::map<std::string, std::string> m;
  for (int i = 1; i + 1 < argc; i += 2) {
    std::string k = argv[i];
    if (k.rfind("--", 0) != 0) continue;
    m[k.substr(2)] = argv[i + 1];
  }
  for (int i = 1; i < argc; ++i) {
    std::string k = argv[i];
    if (k == "--trace" || k == "--help" || k == "--version") m[k.substr(2)] = "1";
  }
  return m;
}

void print_help() {
  std::cout
    << "Bluestein FFT backend " FFT_VERSION "\n"
    << "Options:\n"
    << "  --length N       transform length (required)\n"
    << "  --direction fwd|inv   forward (default) or inverse\n"
    << "  --generator random|impulse|large-dynamic\n"
    << "  --repeats R      timing repetitions (default 1)\n"
    << "  --seed S         deterministic RNG seed (default 12345)\n"
    << "  --request-id ID  caller request identity\n"
    << "  --show N         print first N output samples\n"
    << "  --trace          print step-by-step trace\n"
    << "  --help --version\n";
}
} // namespace

int main(int argc, char** argv) {
  const auto args = parse(argc, argv);
  if (args.count("help")) { print_help(); return 0; }
  if (args.count("version")) {
    std::cout << FFT_VERSION << "\n";
    return 0;
  }
  if (!args.count("length")) {
    std::cerr << "FAILURE category=USAGE detail=\"--length is required\"\n";
    return 2;
  }

  fft::BenchConfig cfg;
  cfg.n = static_cast<std::size_t>(std::strtoull(args.at("length").c_str(),
                                                 nullptr, 10));
  cfg.forward = (args.count("direction") ? args.at("direction") : "fwd")
                    != "inv";
  cfg.generator = args.count("generator") ? args.at("generator") : "random";
  cfg.repeats = args.count("repeats")
                    ? std::strtoul(args.at("repeats").c_str(), nullptr, 10)
                    : 1;
  cfg.seed = args.count("seed")
                 ? static_cast<unsigned>(std::strtoul(
                       args.at("seed").c_str(), nullptr, 10))
                 : 12345u;
  std::string rid = args.count("request-id") ? args.at("request-id")
                                             : fft::make_request_id();

  // Drive the facade directly so the trace is available; benchmark reports
  // memory and timing.
  auto x = fft::make_synthetic(cfg.n, cfg.generator, cfg.seed);
  std::vector<Complex> y(cfg.n);
  fft::StepTrace trace;

  std::cout << "REQUEST id=" << rid << " version=" FFT_VERSION
            << " service=fft_cli location=main\n";

  const auto res = fft::transform(
      x, y, cfg.forward ? fft::Direction::Forward : fft::Direction::Inverse,
      rid, args.count("trace") ? &trace : nullptr);

  if (args.count("trace"))
    for (const auto& s : trace.steps) std::cout << "STEP " << s << "\n";

  if (res.code != fft::ErrorCode::Ok) {
    fft::ErrorInfo err{res.code, res.message, "fft::transform", rid};
    std::cout << fft::format_failure(err) << "\n";
    std::cout << "RESULT status=FAILURE request=" << rid << "\n";
    return 1;
  }

  std::cout << "ALGORITHM name=" << res.algorithm
            << " N=" << cfg.n << " M=" << res.convolution_length << "\n";
  std::cout << "MEMORY input_bytes=" << res.memory.input_bytes
            << " output_bytes=" << res.memory.output_bytes
            << " working_set_bytes=" << res.memory.working_set_bytes
            << " peak_total_bytes=" << res.memory.peak_total_bytes
            << " process_peak_rss_bytes=" << fft::process_peak_rss_bytes()
            << "\n";

  // Independent reference error + uncertainty.
  const auto rep = fft::run_benchmark(cfg, 1e-9, 1e-11);
  if (rep.reference_used) {
    std::cout << "ERROR max_abs=" << rep.error.max_abs_err
              << " max_rel=" << rep.error.max_rel_err
              << " rms=" << rep.error.rms_err
              << " ref_scale=" << rep.error.ref_scale << "\n";
  } else {
    std::cout << "ERROR reference=unavailable\n";
  }
  std::cout << "UNCERTAINTY band=" << rep.uncertainty.band
            << " acceptable=" << (rep.uncertainty.acceptable ? "yes" : "no")
            << " reason=\"" << rep.uncertainty.reason << "\"\n";

  const std::size_t show = args.count("show")
                               ? std::strtoull(args.at("show").c_str(),
                                               nullptr, 10)
                               : 0;
  for (std::size_t k = 0; k < show && k < y.size(); ++k)
    std::cout << "SAMPLE k=" << k << " re=" << y[k].real()
              << " im=" << y[k].imag() << "\n";

  std::cout << "RESULT status=OK request=" << rid
            << " time_ms_med=" << rep.time_ms_med << "\n";
  return rep.uncertainty.acceptable ? 0 : 3;
}
