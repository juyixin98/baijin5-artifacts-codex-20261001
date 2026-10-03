#pragma once
// Numeric contract module: compares a candidate FFT (any implementation)
// against the independent long-double direct DFT and renders an explainable
// verdict. It does not itself compute FFTs, so it stays an honest judge.
#include "common/status.hpp"
#include <complex>
#include <cstddef>
#include <vector>

namespace fft::contract {

struct ErrorMetrics {
  std::size_t n = 0;
  double maxAbsError = 0.0;     // max_k |cand - ref|
  double rmsAbsError = 0.0;     // sqrt(mean |cand-ref|^2)
  double maxRelError = 0.0;     // max_k |cand-ref| / max(1, |ref|)
  double referenceScale = 0.0;  // max_k |ref|
  double conditionEstimate = 0.0; // max|X| / mean|X|, signals dynamic range
  std::size_t worstBin = 0;
};

struct Verdict {
  common::FftStatus category = common::FftStatus::Ok;
  ErrorMetrics metrics;
  // Human-readable, failure/uncertainty listed separately from success.
  std::string summary;
  std::string failureReason; // empty on full success
  bool uncertain = false;    // produced a result but not provably within tol
};

struct ContractTolerances {
  double passAbsTol = 1e-8;       // exact agreement band
  double riskAbsTol = 1e-4;       // between pass and risk => precision risk
  double maxAllowedAbs = 1e-2;    // above => reference mismatch
};

ErrorMetrics measureAgainstReference(
    const std::vector<std::complex<double>>& candidate,
    const std::vector<std::complex<long double>>& reference);

Verdict judge(const ErrorMetrics& m, const ContractTolerances& t = {});

// Contract checks on the convolution geometry and normalization.
struct GeometryCheck {
  bool linearConvolutionSatisfied = false;
  std::size_t requiredLength = 0; // 2N-1
  std::size_t convolutionLength = 0;
  std::string detail;
};
GeometryCheck checkConvolutionGeometry(std::size_t n, std::size_t convLen);

// ifft(fft(x)) must reconstruct x; reports max abs round-trip error.
double roundTripError(const std::vector<std::complex<double>>& original,
                      const std::vector<std::complex<double>>& reconstructed);

} // namespace fft::contract
