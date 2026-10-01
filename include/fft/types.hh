// SPDX-License-Identifier: MIT
// Numerical contracts for the Bluestein FFT backend.
// All transforms use the fixed convention:
//   forward:  X[k] = sum_n x[n] * exp(-i 2pi n k / N)   (unscaled)
//   inverse:  x[n] = (1/N) sum_k X[k] * exp(+i 2pi n k / N)
#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace fft {

using Real    = double;
using Complex = std::complex<double>;

// Direction of a transform. Forward is the negative-sign, unscaled DFT.
enum class Direction { Forward, Inverse };

// Structured failure categories. Independent tests assert on these, never on
// free-form log text.
enum class ErrorCode {
  Ok = 0,
  EmptyLength,          // N == 0
  LengthOverflow,       // internal convolution length / index overflow
  AllocationFailed,     // could not allocate working buffers
  NaNOrInfInput,        // input contains non-finite values
  UnsupportedLength,    // length cannot be handled by this backend
  InternalError         // invariant violated inside the kernel
};

struct ErrorInfo {
  ErrorCode   code   = ErrorCode::Ok;
  std::string message;
  std::string location;   // module + function that produced the failure
  std::string request_id; // echoed request identity
};

// Fine-grained diagnostics / trace associated with a single request.
struct StepTrace {
  std::string request_id;
  std::string version;
  std::string location;
  std::vector<std::string> steps;
};

// Memory accounting, in bytes. "working_set" is the peak extra storage the
// kernel allocated beyond the caller's input/output buffers.
struct MemoryReport {
  std::size_t input_bytes        = 0;
  std::size_t output_bytes       = 0;
  std::size_t working_set_bytes  = 0;
  std::size_t peak_total_bytes   = 0;
};

// Result bundle returned by the public entry points.
struct FFTResult {
  ErrorCode    code = ErrorCode::Ok;
  std::string  message;
  MemoryReport memory;
  std::size_t  convolution_length = 0; // M: power-of-two, M >= 2N-1
  std::string  algorithm;             // "radix2" or "bluestein"
};

const char* error_name(ErrorCode code) noexcept;
std::string explain(const ErrorInfo& err);

// Deterministic request id used when the caller supplies none.
std::string make_request_id();

} // namespace fft
