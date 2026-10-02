#pragma once

#include <ostream>
#include <string>
#include <utility>

namespace rlmf {

// Failure categories required by the project contract. Every error reported
// across module boundaries carries one of these stable tags so callers (and
// tests) can distinguish the *kind* of failure rather than matching strings.
enum class ErrorKind {
    None = 0,
    InvalidArgument,   // bad input that violates the documented preconditions
    StateConflict,     // object used outside of its allowed state transition
    ResourceExhausted, // requested allocation is rejected by the environment
    ComputationFailed, // inputs were valid but the numerical procedure broke down
};

const char* error_kind_name(ErrorKind kind) noexcept;

struct Error {
    ErrorKind kind = ErrorKind::None;
    std::string message;
    // Stable machine-readable code, e.g. "rank.out_of_range".
    std::string code;

    Error() = default;
    Error(ErrorKind k, std::string c, std::string msg)
        : kind(k), message(std::move(msg)), code(std::move(c)) {}

    explicit operator bool() const noexcept { return kind != ErrorKind::None; }
};

std::ostream& operator<<(std::ostream& os, const Error& err);

// Minimal Result<T> (std::expected is C++23). T must be default constructible.
template <class T>
class Result {
public:
    Result(T value) : value_(std::move(value)), ok_(true) {}
    Result(Error error) : error_(std::move(error)), ok_(false) {}

    bool ok() const noexcept { return ok_; }
    explicit operator bool() const noexcept { return ok_; }

    const T& value() const { return value_; }
    T& value() { return value_; }
    const Error& error() const { return error_; }

private:
    T value_{};
    Error error_{};
    bool ok_ = false;
};

} // namespace rlmf
