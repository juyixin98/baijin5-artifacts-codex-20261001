#pragma once
// Service-level response envelope. Separates success, failure and
// uncertainty so clients never parse free-form text to decide control flow.
#include "common/request.hpp"
#include "common/status.hpp"
#include <string>

namespace fft::service {

struct Response {
  std::string requestId;
  std::string version = BLUESTEIN_FFT_VERSION_STRING;
  common::FftStatus status = common::FftStatus::Ok;
  bool uncertain = false;
  std::string kernelPath;
  std::size_t length = 0;
  std::size_t convolutionLength = 0;
  std::size_t peakWorkspaceBytes = 0;
  double maxAbsError = -1.0;
  double roundTripError = -1.0;
  std::string failureReason; // populated on failure / uncertainty
  std::string detail;

  void emitJson(std::FILE* sink, bool pretty) const;
};

} // namespace fft::service
