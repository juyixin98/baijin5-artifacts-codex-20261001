#pragma once

// Error contract shared by every module of the library.
//
// Every fallible operation returns gauss::Result<T>. On failure the caller
// receives a gauss::Error whose *category* is part of the public contract:
// callers and tests must be able to distinguish why an operation failed
// without parsing message text. The four categories are:
//
//   kInvalidInput       caller supplied arguments outside the documented domain
//   kStateConflict      the object's current state forbids the operation
//   kResourceExhaustion the request exceeds configured resource limits or
//                       memory allocation failed
//   kComputationFailure the numerical algorithm itself failed (e.g. a Newton
//                       iteration did not converge); no partial result is
//                       ever returned in this case

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace gauss {

enum class ErrorCategory {
  kInvalidInput,
  kStateConflict,
  kResourceExhaustion,
  kComputationFailure,
};

std::string_view to_string(ErrorCategory category) noexcept;

struct Error {
  ErrorCategory category = ErrorCategory::kComputationFailure;
  std::string message;
  // Key intermediate state captured at the failure point (iteration counts,
  // residuals, configured limits, ...). Recorded so a failure can be
  // replayed and diagnosed from logs without re-running the computation.
  std::vector<std::pair<std::string, std::string>> diagnostics;

  Error& with_diagnostic(std::string key, std::string value) & {
    diagnostics.emplace_back(std::move(key), std::move(value));
    return *this;
  }
  Error with_diagnostic(std::string key, std::string value) && {
    diagnostics.emplace_back(std::move(key), std::move(value));
    return std::move(*this);
  }

  std::string to_string() const;
};

template <typename T>
class [[nodiscard]] Result {
 public:
  static Result ok(T value) { return Result(std::move(value)); }
  static Result fail(Error error) { return Result(std::move(error)); }

  bool has_value() const noexcept { return value_.has_value(); }
  explicit operator bool() const noexcept { return has_value(); }

  const T& value() const& {
    if (!value_) throw std::logic_error("gauss::Result: value() on an error result");
    return *value_;
  }
  T& value() & {
    if (!value_) throw std::logic_error("gauss::Result: value() on an error result");
    return *value_;
  }
  T&& value() && {
    if (!value_) throw std::logic_error("gauss::Result: value() on an error result");
    return std::move(*value_);
  }

  const Error& error() const& {
    if (value_) throw std::logic_error("gauss::Result: error() on an ok result");
    return error_;
  }

 private:
  explicit Result(T value) : value_(std::move(value)) {}
  explicit Result(Error error) : error_(std::move(error)) {}

  std::optional<T> value_;
  Error error_{};
};

}  // namespace gauss
