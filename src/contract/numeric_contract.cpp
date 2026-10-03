#include "numeric_contract.hpp"
#include <cmath>
#include <sstream>

namespace fft::contract {

ErrorMetrics measureAgainstReference(
    const std::vector<std::complex<double>>& candidate,
    const std::vector<std::complex<long double>>& reference) {
  ErrorMetrics m;
  const std::size_t n = std::min(candidate.size(), reference.size());
  m.n = n;
  double sumSq = 0.0;
  double sumMag = 0.0;
  for (std::size_t k = 0; k < n; ++k) {
    const std::complex<double> r(static_cast<double>(reference[k].real()),
                                 static_cast<double>(reference[k].imag()));
    const double e = std::abs(candidate[k] - r);
    const double rm = std::abs(r);
    if (e > m.maxAbsError) {
      m.maxAbsError = e;
      m.worstBin = k;
    }
    const double rel = e / std::max(1.0, rm);
    m.maxRelError = std::max(m.maxRelError, rel);
    m.referenceScale = std::max(m.referenceScale, rm);
    sumSq += e * e;
    sumMag += rm;
  }
  m.rmsAbsError = n ? std::sqrt(sumSq / static_cast<double>(n)) : 0.0;
  const double meanMag = n ? sumMag / static_cast<double>(n) : 0.0;
  m.conditionEstimate = meanMag > 0 ? m.referenceScale / meanMag : 0.0;
  return m;
}

Verdict judge(const ErrorMetrics& m, const ContractTolerances& t) {
  Verdict v;
  v.metrics = m;
  std::ostringstream ok;
  ok << "N=" << m.n << " maxAbs=" << m.maxAbsError
     << " rmsAbs=" << m.rmsAbsError << " maxRel=" << m.maxRelError
     << " worstBin=" << m.worstBin << " scale=" << m.referenceScale
     << " condition=" << m.conditionEstimate;
  v.summary = ok.str();

  if (m.maxAbsError <= t.passAbsTol) {
    v.category = common::FftStatus::Ok;
    return v;
  }
  if (m.maxAbsError <= t.riskAbsTol) {
    v.category = common::FftStatus::PrecisionRisk;
    v.uncertain = true;
    std::ostringstream f;
    f << "error " << m.maxAbsError << " lies between pass band "
      << t.passAbsTol << " and risk band " << t.riskAbsTol
      << "; result not provably within contract (worst bin " << m.worstBin
      << ")";
    v.failureReason = f.str();
    return v;
  }
  if (m.maxAbsError <= t.maxAllowedAbs) {
    v.category = common::FftStatus::PrecisionRisk;
    v.uncertain = true;
    std::ostringstream f;
    f << "elevated error " << m.maxAbsError << " within hard ceiling "
      << t.maxAllowedAbs << " but outside risk band; treated as uncertainty";
    v.failureReason = f.str();
    return v;
  }
  v.category = common::FftStatus::ReferenceMismatch;
  std::ostringstream f;
  f << "candidate disagrees with independent reference: maxAbs "
    << m.maxAbsError << " > ceiling " << t.maxAllowedAbs
    << " (worst bin " << m.worstBin << ")";
  v.failureReason = f.str();
  return v;
}

GeometryCheck checkConvolutionGeometry(std::size_t n, std::size_t convLen) {
  GeometryCheck g;
  g.requiredLength = n == 0 ? 0 : 2 * n - 1;
  g.convolutionLength = convLen;
  g.linearConvolutionSatisfied = convLen >= g.requiredLength;
  std::ostringstream d;
  d << "N=" << n << " requires conv length >= " << g.requiredLength
    << " for linear convolution; using " << convLen;
  g.detail = d.str();
  return g;
}

double roundTripError(const std::vector<std::complex<double>>& original,
                      const std::vector<std::complex<double>>& reconstructed) {
  double e = 0.0;
  const std::size_t n = std::min(original.size(), reconstructed.size());
  for (std::size_t i = 0; i < n; ++i)
    e = std::max(e, std::abs(original[i] - reconstructed[i]));
  return e;
}

} // namespace fft::contract
