#include "numcontract/failure.h"

namespace mp::contract {

const char* fail_code(Fail f) noexcept {
  switch (f) {
  case Fail::None: return "NONE";
  case Fail::MalformedRequest: return "MALFORMED_REQUEST";
  case Fail::DuplicateJobId: return "DUPLICATE_JOB_ID";
  case Fail::MalformedCoefficient: return "MALFORMED_COEFFICIENT";
  case Fail::MalformedPoint: return "MALFORMED_POINT";
  case Fail::EmptyCoefficients: return "EMPTY_COEFFICIENTS";
  case Fail::InvalidModulus: return "INVALID_MODULUS";
  case Fail::NonPrimeModulus: return "NON_PRIME_MODULUS";
  case Fail::ModulusTooLarge: return "MODULUS_TOO_LARGE";
  case Fail::DomainMismatch: return "DOMAIN_MISMATCH";
  case Fail::InfeasibleBatchLimit: return "INFEASIBLE_BATCH_LIMIT";
  case Fail::InternalError: return "INTERNAL_ERROR";
  }
  return "UNKNOWN_FAILURE";
}

const char* fail_explain(Fail f) noexcept {
  switch (f) {
  case Fail::None: return "no failure";
  case Fail::MalformedRequest:
    return "request text violates the line-based grammar (unknown/duplicated directive, bad shape).";
  case Fail::DuplicateJobId:
    return "two jobs in the same request declare the same JOB identity.";
  case Fail::MalformedCoefficient:
    return "a COEFF token is not a syntactically valid integer literal.";
  case Fail::MalformedPoint:
    return "a POINT token is not a syntactically valid integer literal.";
  case Fail::EmptyCoefficients:
    return "a job provides evaluation points but never declares coefficients.";
  case Fail::InvalidModulus:
    return "a FIELD job is missing a modulus, or the modulus value is not a positive integer.";
  case Fail::NonPrimeModulus:
    return "the declared modulus is composite; the prime-field ring contract is broken.";
  case Fail::ModulusTooLarge:
    return "the modulus is >= 2^63; products would overflow the 64-bit field representation.";
  case Fail::DomainMismatch:
    return "exact-integer and prime-field declarations are mixed inside one job.";
  case Fail::InfeasibleBatchLimit:
    return "the memory limit is too small to evaluate even a single point.";
  case Fail::InternalError:
    return "an internal invariant of the evaluator was violated.";
  }
  return "unclassified failure";
}

} // namespace mp::contract
