// SPDX-License-Identifier: MIT
//
// Diagnostics: every recorded event carries a request id, severity, and the
// key state motivating an accept/reject/inconclusive decision. Secret-bearing
// fields must pass through redact() before being emitted.
#ifndef CHEBCORE_DIAGNOSTICS_HPP
#define CHEBCORE_DIAGNOSTICS_HPP

#include <chrono>
#include <cstdint>
#include <iomanip>
#include <ostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace chebcore::diag {

enum class Severity { Info, Warn, Error };

std::string_view to_string(Severity s);

// Masks a sensitive value for logs: keeps a short prefix and length only.
// Empty input renders as "<empty>", never echoing the payload.
std::string redact(std::string_view secret, std::size_t keep_prefix = 2);

// Deterministic, environment-independent request id (counter + caller tag).
// A timestamp wall-clock string is attached for human reading, but decisions
// never depend on it.
std::string new_request_id(std::string_view tag = "req");

struct Event {
  std::string request_id;
  Severity severity{Severity::Info};
  std::string code;
  std::string message;
  std::string key_state;
};

class DecisionLog {
 public:
  explicit DecisionLog(std::string request_id = new_request_id())
      : request_id_(std::move(request_id)) {}

  const std::string& request_id() const noexcept { return request_id_; }

  void record(Severity s, std::string code, std::string message,
              std::string key_state = {}) {
    events_.push_back(Event{request_id_, s, std::move(code), std::move(message),
                            std::move(key_state)});
  }

  const std::vector<Event>& events() const noexcept { return events_; }
  bool has_error() const noexcept;

  void emit(std::ostream& os) const;

 private:
  std::string request_id_;
  std::vector<Event> events_;
};

std::string wall_time_utc();

}  // namespace chebcore::diag

#endif
