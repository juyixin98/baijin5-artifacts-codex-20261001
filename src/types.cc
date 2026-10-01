// SPDX-License-Identifier: MIT
#include "fft/types.hh"

#include <atomic>
#include <chrono>
#include <cmath>

namespace fft {

const char* error_name(ErrorCode code) noexcept {
  switch (code) {
    case ErrorCode::Ok:                return "OK";
    case ErrorCode::EmptyLength:       return "EMPTY_LENGTH";
    case ErrorCode::LengthOverflow:    return "LENGTH_OVERFLOW";
    case ErrorCode::AllocationFailed:  return "ALLOCATION_FAILED";
    case ErrorCode::NaNOrInfInput:     return "NAN_OR_INF_INPUT";
    case ErrorCode::UnsupportedLength: return "UNSUPPORTED_LENGTH";
    case ErrorCode::InternalError:     return "INTERNAL_ERROR";
  }
  return "UNKNOWN_ERROR";
}

std::string explain(const ErrorInfo& err) {
  std::string s = "[";
  s += error_name(err.code);
  s += "] ";
  if (!err.request_id.empty()) {
    s += "request=";
    s += err.request_id;
    s += " ";
  }
  if (!err.location.empty()) {
    s += "at ";
    s += err.location;
    s += ": ";
  }
  s += err.message;
  return s;
}

namespace {
std::atomic<std::uint64_t> g_seq{0};
}

std::string make_request_id() {
  using namespace std::chrono;
  const auto now = system_clock::now().time_since_epoch();
  const auto ms  = duration_cast<milliseconds>(now).count();
  const auto seq = g_seq.fetch_add(1, std::memory_order_relaxed) + 1;
  char buf[64];
  std::snprintf(buf, sizeof(buf), "req-%013lld-%04llx",
                static_cast<long long>(ms),
                static_cast<unsigned long long>(seq & 0xffffULL));
  return std::string(buf);
}

} // namespace fft
