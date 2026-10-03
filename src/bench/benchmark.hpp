#pragma once
// Independent benchmark + fixture module.
//
// Provides:
//  * deterministic synthetic fixtures (no external data),
//  * analytic oracles for fixtures that must hold for ANY FFT implementation
//    (impulse -> all ones; unit complex exponential -> single DFT bin),
//  * wall-clock timing and RSS memory measurement,
//  * benchmark reports that reference the independent long-double DFT for
//    small sizes and analytic properties for large sizes.
#include "common/status.hpp"
#include "common/version.hpp"
#include <complex>
#include <cstddef>
#include <string>
#include <vector>

namespace fft::bench {

using Cmplx = std::complex<double>;

enum class FixtureKind {
  Impulse,       // delta at given index
  Constant,      // all ones
  ComplexTone,   // exp(i 2pi f0 n / N): DFT is N at bin f0
  CosineReal,    // real cosine
  LargeDynamic,  // impulse + tiny tone: stresses dynamic range
  PseudoRandom   // deterministic LCG noise
};

struct Fixture {
  FixtureKind kind = FixtureKind::Impulse;
  std::size_t n = 0;
  long long seed = 1234567;
  std::size_t impulseIndex = 0;
  std::size_t toneBin = 1;
  double tinyAmplitude = 1e-12;
  std::vector<Cmplx> samples;
  std::string description;
};

Fixture generateFixture(FixtureKind kind, std::size_t n,
                        long long seed = 1234567,
                        std::size_t param = 0);

struct AnalyticCheck {
  bool passed = false;
  double maxResidual = 0.0;
  std::string detail;
};
// Verifies DFT output against the closed form implied by the fixture kind.
AnalyticCheck checkAnalyticSpectrum(const Fixture& fx,
                                    const std::vector<Cmplx>& X);

struct MemorySample {
  long rssBytesNow = 0; // process RSS at sample time
};
MemorySample sampleMemory();

struct BenchRecord {
  std::size_t n = 0;
  const char* kernelPath = "";
  std::size_t convolutionLength = 0;
  std::size_t peakWorkspaceBytes = 0;
  long rssPeakBytes = 0;
  double forwardMs = 0.0;
  double inverseMs = 0.0;
  double maxAbsVsReference = -1.0; // only when reference run
  double roundTripError = 0.0;
  common::FftStatus status = common::FftStatus::Ok;
  std::string note;
};

struct BenchReport {
  std::string requestId;
  std::string version;
  std::vector<BenchRecord> records;
  long rssBeforeBytes = 0;
  long rssAfterBytes = 0;
};

// Runs the mathcore FFT over the supplied lengths. For lengths up to
// referenceMaxN it compares against the independent direct DFT; otherwise it
// uses analytic fixture properties.
BenchReport runBenchmark(const std::vector<std::size_t>& lengths,
                         std::size_t referenceMaxN,
                         const std::string& requestId);

} // namespace fft::bench
