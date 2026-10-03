#include "status.hpp"

namespace fft::common {

const char* statusName(FftStatus s) noexcept {
  switch (s) {
  case FftStatus::Ok: return "OK";
  case FftStatus::EmptyInput: return "EMPTY_INPUT";
  case FftStatus::InvalidLength: return "INVALID_LENGTH";
  case FftStatus::InvalidArgument: return "INVALID_ARGUMENT";
  case FftStatus::ValueOutOfDomain: return "VALUE_OUT_OF_DOMAIN";
  case FftStatus::AllocationFailed: return "ALLOCATION_FAILED";
  case FftStatus::PrecisionRisk: return "PRECISION_RISK";
  case FftStatus::ReferenceMismatch: return "REFERENCE_MISMATCH";
  case FftStatus::InternalError: return "INTERNAL_ERROR";
  case FftStatus::NotImplemented: return "NOT_IMPLEMENTED";
  }
  return "UNKNOWN_STATUS";
}

std::size_t defaultMemoryBudget() noexcept {
  // Generous but bounded: 2 GiB.
  return static_cast<std::size_t>(2) * 1024 * 1024 * 1024;
}

} // namespace fft::common
