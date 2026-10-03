#pragma once
#include <complex>
#include <string>
#include <vector>

namespace fft::common {

// Exhaustive, explicit failure taxonomy. Callers must branch on the category
// rather than parsing human readable text.
enum class FftStatus {
  Ok = 0,
  EmptyInput,       // length zero: a DFT is not defined
  InvalidLength,    // negative/unsupported length argument
  InvalidArgument,  // null buffers, bad direction, etc.
  ValueOutOfDomain, // NaN or Inf found in the input
  AllocationFailed, // requested work buffers exceed the memory budget
  PrecisionRisk,    // result produced but error budget is not provably met
  ReferenceMismatch,// verification: SUT and independent reference disagree
  InternalError,    // invariant violation inside the implementation
  NotImplemented     // reserved / unimplemented path hit
};

const char* statusName(FftStatus s) noexcept;

struct FftError {
  FftStatus status = FftStatus::Ok;
  std::string message;
};

// Fixed forward/inverse normalization contract:
//   forward: X[k] = sum_n x[n] exp(-i 2pi n k / N)   (no scale)
//   inverse: x[n] = (1/N) sum_k X[k] exp(+i 2pi n k / N)
struct FftOptions {
  // Maximum total heap (bytes) the routine is allowed to reserve internally.
  // 0 means "use the built-in default budget".
  std::size_t memoryBudgetBytes = 0;
};

std::size_t defaultMemoryBudget() noexcept;

} // namespace fft::common
