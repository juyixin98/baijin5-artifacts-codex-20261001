#pragma once
// Numeric contract: failure classification shared by every backend module.
#include <string>

namespace mp::contract {

// Exhaustive, stable failure category codes. Tests assert against these by
// name (never by message text), so the contract stays machine-checkable.
enum class Fail {
  None = 0,
  MalformedRequest,        // structural / directive / unknown key problems
  DuplicateJobId,          // JOB id repeats within one request
  MalformedCoefficient,    // COEFF token is not a valid integer literal
  MalformedPoint,          // POINT token is not a valid integer literal
  EmptyCoefficients,       // job declares no COEFF line
  InvalidModulus,          // field job without usable modulus, or negative/zero
  NonPrimeModulus,         // modulus is not prime
  ModulusTooLarge,         // modulus >= 2^63 (product would overflow uint64)
  DomainMismatch,          // domain/modulus declarations disagree in one job
  InfeasibleBatchLimit,    // memory limit cannot hold even a one-point batch
  InternalError,           // invariant broken by the implementation
};

// Stable machine-readable token for the failure category.
const char* fail_code(Fail f) noexcept;

// Human-readable explanation of what the category means.
const char* fail_explain(Fail f) noexcept;

struct Failure {
  Fail code{Fail::None};
  std::string detail;     // human detail, may mention line numbers / values
  std::string location;   // e.g. "request:line 7" or "job 'j1'"
  std::string request_id; // request identity this failure belongs to

  explicit operator bool() const noexcept { return code != Fail::None; }
};

} // namespace mp::contract
