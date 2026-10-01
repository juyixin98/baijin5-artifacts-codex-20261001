// SPDX-License-Identifier: MIT
// Error interpretation module: maps structured failures to human readable
// semantics and describes residual numerical uncertainty separately.
#pragma once

#include "fft/types.hh"

#include <string>
#include <vector>

namespace fft {

struct ErrorCategory {
  ErrorCode   code;
  std::string name;
  std::string meaning;    // what it means to a caller
  std::string remediation;
  bool        retryable;
};

const ErrorCategory& categorize(ErrorCode code);

// A single, explicit explanation line for a failure, carrying request id.
std::string format_failure(const ErrorInfo& err);

struct ErrorStats {
  double max_abs_err   = 0.0;
  double max_rel_err   = 0.0;
  double rms_err       = 0.0;
  double ref_scale     = 1.0; // reference magnitude scale
  bool   reference_available = true;
};

struct UncertaintyVerdict {
  bool   acceptable = true;
  std::string band; // e.g. "roundoff", "elevated", "untrusted"
  std::string reason;
};

// Interpret residuals independently from hard failures. "uncertain" results
// (elevated but not failing error, or missing reference) are reported on
// their own so they are never confused with error-code failures.
UncertaintyVerdict interpret_uncertainty(const ErrorStats& st,
                                         double tol_abs,
                                         double tol_rel);

} // namespace fft
