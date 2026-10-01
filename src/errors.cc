// SPDX-License-Identifier: MIT
#include "fft/errors.hh"

#include <cmath>

namespace fft {

const ErrorCategory& categorize(ErrorCode code) {
  static const ErrorCategory table[] = {
    {ErrorCode::Ok,                "OK",
      "transform completed", "none", false},
    {ErrorCode::EmptyLength,       "EMPTY_LENGTH",
      "requested transform length was zero",
      "supply a positive length", false},
    {ErrorCode::LengthOverflow,    "LENGTH_OVERFLOW",
      "convolution padding length exceeded addressable range",
      "reduce the transform length", false},
    {ErrorCode::AllocationFailed,  "ALLOCATION_FAILED",
      "working buffers could not be allocated",
      "reduce length or free memory; request may be retried", true},
    {ErrorCode::NaNOrInfInput,     "NAN_OR_INF_INPUT",
      "an input sample was NaN or infinite",
      "sanitize the input; output is undefined", false},
    {ErrorCode::UnsupportedLength, "UNSUPPORTED_LENGTH",
      "length or buffer shape is not supported",
      "match output size to input size and use a valid length", false},
    {ErrorCode::InternalError,     "INTERNAL_ERROR",
      "a kernel invariant was violated or non-finite output appeared",
      "capture the trace and report the version", false},
  };
  for (const auto& c : table)
    if (c.code == code) return c;
  return table[0];
}

std::string format_failure(const ErrorInfo& err) {
  const auto& cat = categorize(err.code);
  std::string s = "FAILURE";
  if (!err.request_id.empty()) s += " request=" + err.request_id;
  s += std::string(" category=") + cat.name;
  if (!err.location.empty()) s += " at=" + err.location;
  s += " meaning=\"" + cat.meaning + "\"";
  if (!err.message.empty()) s += " detail=\"" + err.message + "\"";
  s += std::string(" retryable=") + (cat.retryable ? "yes" : "no");
  return s;
}

UncertaintyVerdict interpret_uncertainty(const ErrorStats& st,
                                         double tol_abs,
                                         double tol_rel) {
  UncertaintyVerdict v;
  if (!st.reference_available) {
    v.acceptable = false;
    v.band = "untrusted";
    v.reason = "no independent reference available; correctness unverified";
    return v;
  }
  const double combined = tol_abs + tol_rel * std::max(1.0, st.ref_scale);
  if (st.max_abs_err <= combined) {
    v.acceptable = true;
    v.band = "roundoff";
    v.reason = "residual within expected floating-point round-off band";
  } else if (st.max_abs_err <= 100.0 * combined ||
             (st.ref_scale > 0 && st.max_rel_err <= 100.0 * tol_rel)) {
    v.acceptable = true;
    v.band = "elevated";
    v.reason = "residual above nominal tolerance but within 100x guard band; "
               "treat as uncertain, do not treat as a hard failure";
  } else {
    v.acceptable = false;
    v.band = "untrusted";
    v.reason = "residual exceeds guard band; result is not trustworthy";
  }
  return v;
}

} // namespace fft
