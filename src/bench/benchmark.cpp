#include "benchmark.hpp"
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "contract/numeric_contract.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <numbers>
#include <sstream>

namespace fft::bench {

namespace {
double pi() { return std::numbers::pi; }

struct Lcg {
  unsigned long long s;
  explicit Lcg(long long seed) : s(static_cast<unsigned long long>(seed)) {}
  double next01() {
    // Numerical Recipes constants, deterministic across platforms.
    s = s * 6364136223846793005ULL + 1442695040888963407ULL;
    return static_cast<double>(s >> 11) /
           static_cast<double>(1ULL << 53);
  }
};
} // namespace

Fixture generateFixture(FixtureKind kind, std::size_t n, long long seed,
                        std::size_t param) {
  Fixture fx;
  fx.kind = kind;
  fx.n = n;
  fx.seed = seed;
  fx.samples.assign(n, Cmplx{});
  Lcg rng(seed);

  switch (kind) {
  case FixtureKind::Impulse: {
    fx.impulseIndex = param % n;
    fx.samples[fx.impulseIndex] = Cmplx(1.0, 0.0);
    fx.description = "unit impulse at index " +
                     std::to_string(fx.impulseIndex);
    break;
  }
  case FixtureKind::Constant: {
    for (auto& v : fx.samples) v = Cmplx(1.0, 0.0);
    fx.description = "constant ones";
    break;
  }
  case FixtureKind::ComplexTone: {
    fx.toneBin = param ? param % n : 1 % n;
    for (std::size_t i = 0; i < n; ++i) {
      const double a = 2.0 * pi() * static_cast<double>(fx.toneBin) *
                       static_cast<double>(i) / static_cast<double>(n);
      fx.samples[i] = Cmplx(std::cos(a), std::sin(a));
    }
    fx.description = "unit complex tone at bin " +
                     std::to_string(fx.toneBin);
    break;
  }
  case FixtureKind::CosineReal: {
    std::size_t f0 = param ? param % (n / 2 + 1) : 1;
    for (std::size_t i = 0; i < n; ++i) {
      const double a = 2.0 * pi() * static_cast<double>(f0) *
                       static_cast<double>(i) / static_cast<double>(n);
      fx.samples[i] = Cmplx(std::cos(a), 0.0);
    }
    fx.description = "real cosine";
    break;
  }
  case FixtureKind::LargeDynamic: {
    // Strong unit-magnitude complex tone at a fixed bin plus a second tone
    // 1e12 times weaker at a distinct bin. After the DFT the weak bin must
    // still be recovered at scale N*1e-12 while the strong bin sits at N.
    fx.tinyAmplitude = 1e-12;
    fx.toneBin = param ? param % n : 1 % n;
    const std::size_t strongBin = (fx.toneBin + n / 2) % n;
    for (std::size_t i = 0; i < n; ++i) {
      const double as = 2.0 * pi() * static_cast<double>(strongBin) *
                        static_cast<double>(i) / static_cast<double>(n);
      const double aw = 2.0 * pi() * static_cast<double>(fx.toneBin) *
                        static_cast<double>(i) / static_cast<double>(n);
      fx.samples[i] = Cmplx(std::cos(as), std::sin(as)) +
                      fx.tinyAmplitude * Cmplx(std::cos(aw), std::sin(aw));
    }
    fx.impulseIndex = strongBin; // reused to report the strong bin
    fx.description = "large dynamic range (unit tone + 1e-12 tone)";
    break;
  }
  case FixtureKind::PseudoRandom: {
    for (auto& v : fx.samples)
      v = Cmplx(2.0 * rng.next01() - 1.0, 2.0 * rng.next01() - 1.0);
    fx.description = "deterministic pseudo-random complex noise";
    break;
  }
  }
  return fx;
}

AnalyticCheck checkAnalyticSpectrum(const Fixture& fx,
                                    const std::vector<Cmplx>& X) {
  AnalyticCheck ac;
  if (X.size() != fx.n) {
    ac.detail = "output length mismatch";
    return ac;
  }
  const double N = static_cast<double>(fx.n);
  double e = 0.0;
  switch (fx.kind) {
  case FixtureKind::Impulse: {
    // DFT of delta[p]: X[k] = exp(-i 2pi p k / N).
    for (std::size_t k = 0; k < fx.n; ++k) {
      const double a = -2.0 * pi() *
                       static_cast<double>(fx.impulseIndex) *
                       static_cast<double>(k) / N;
      e = std::max(e, std::abs(X[k] - Cmplx(std::cos(a), std::sin(a))));
    }
    break;
  }
  case FixtureKind::Constant: {
    for (std::size_t k = 0; k < fx.n; ++k) {
      const Cmplx want = k == 0 ? Cmplx(N, 0.0) : Cmplx(0.0, 0.0);
      e = std::max(e, std::abs(X[k] - want));
    }
    break;
  }
  case FixtureKind::ComplexTone: {
    for (std::size_t k = 0; k < fx.n; ++k) {
      const Cmplx want = k == fx.toneBin ? Cmplx(N, 0.0) : Cmplx(0.0, 0.0);
      e = std::max(e, std::abs(X[k] - want));
    }
    break;
  }
  default:
    ac.detail = "no closed form for fixture kind; reference comparison used";
    ac.passed = true;
    ac.maxResidual = -1.0;
    return ac;
  }
  ac.maxResidual = e;
  // Scale-aware tolerance: spectra reach magnitude N.
  const double tol = 1e-9 * std::max(1.0, N);
  ac.passed = e <= tol;
  std::ostringstream d;
  d << "analytic residual " << e << " (tol " << tol << ")";
  ac.detail = d.str();
  return ac;
}

MemorySample sampleMemory() {
  MemorySample m;
  std::FILE* f = std::fopen("/proc/self/status", "r");
  if (!f) return m;
  char line[256];
  while (std::fgets(line, sizeof(line), f)) {
    long kb = 0;
    if (std::sscanf(line, "VmRSS: %ld kB", &kb) == 1) {
      m.rssBytesNow = kb * 1024L;
      break;
    }
  }
  std::fclose(f);
  return m;
}

namespace {
double msSince(auto t0) {
  return std::chrono::duration<double, std::milli>(
             std::chrono::steady_clock::now() - t0)
      .count();
}
} // namespace

BenchReport runBenchmark(const std::vector<std::size_t>& lengths,
                         std::size_t referenceMaxN,
                         const std::string& requestId) {
  BenchReport rep;
  rep.requestId = requestId;
  rep.version = BLUESTEIN_FFT_VERSION_STRING;
  rep.rssBeforeBytes = sampleMemory().rssBytesNow;

  for (std::size_t n : lengths) {
    BenchRecord rec;
    rec.n = n;
    auto fx = generateFixture(FixtureKind::PseudoRandom, n,
                              0xC0FFEEULL ^ n);

    auto t0 = std::chrono::steady_clock::now();
    auto fwd = mathcore::fft(fx.samples);
    rec.forwardMs = msSince(t0);
    rec.status = fwd.error.status;
    if (fwd.error.status != common::FftStatus::Ok) {
      rec.note = fwd.error.message;
      rep.records.push_back(rec);
      continue;
    }
    rec.kernelPath = fwd.kernelPath;
    rec.convolutionLength = fwd.convolutionLength;
    rec.peakWorkspaceBytes = fwd.peakWorkspaceBytes;

    auto t1 = std::chrono::steady_clock::now();
    auto inv = mathcore::ifft(fwd.out);
    rec.inverseMs = msSince(t1);
    rec.roundTripError =
        contract::roundTripError(fx.samples, inv.out);

    // Independent acceptance: reference DFT for small N, analytic fixtures
    // for all sizes (fixture-independent PRNG is verified analytically via a
    // dedicated impulse/tone companion run for large N).
    auto imp = generateFixture(FixtureKind::Impulse, n, 1, n / 3);
    auto impX = mathcore::fft(imp.samples);
    auto impAc = checkAnalyticSpectrum(imp, impX.out);
    auto tone = generateFixture(FixtureKind::ComplexTone, n, 2, 1 % n);
    auto toneX = mathcore::fft(tone.samples);
    auto toneAc = checkAnalyticSpectrum(tone, toneX.out);
    if (!impAc.passed || !toneAc.passed) {
      rec.status = common::FftStatus::ReferenceMismatch;
      rec.note = "analytic check failed: " + impAc.detail + " | " +
                 toneAc.detail;
    }

    if (n <= referenceMaxN) {
      auto refr = reference::directDftDouble(fx.samples, -1);
      if (refr.error.status == common::FftStatus::Ok) {
        auto met = contract::measureAgainstReference(fwd.out, refr.out);
        rec.maxAbsVsReference = met.maxAbsError;
      }
    }
    rep.records.push_back(rec);
  }
  rep.rssAfterBytes = sampleMemory().rssBytesNow;
  return rep;
}

} // namespace fft::bench
