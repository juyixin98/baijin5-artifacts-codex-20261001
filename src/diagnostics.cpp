#include "chebcore/diagnostics.hpp"

#include <atomic>
#include <ctime>

namespace chebcore::diag {

namespace {
std::uint64_t counter() {
  static std::atomic<std::uint64_t> c{0};
  return ++c;
}
}  // namespace

std::string_view to_string(Severity s) {
  switch (s) {
    case Severity::Info:
      return "INFO";
    case Severity::Warn:
      return "WARN";
    case Severity::Error:
      return "ERROR";
  }
  return "?";
}

std::string redact(std::string_view secret, std::size_t keep_prefix) {
  if (secret.empty()) return "<empty>";
  std::string out;
  const std::size_t shown = std::min(keep_prefix, secret.size());
  out.append(secret.substr(0, shown));
  out.append("***(").append(std::to_string(secret.size())).append(" chars)");
  return out;
}

std::string new_request_id(std::string_view tag) {
  std::ostringstream os;
  os << tag << "-" << std::setw(8) << std::setfill('0') << counter();
  return os.str();
}

bool DecisionLog::has_error() const noexcept {
  for (const auto& e : events_)
    if (e.severity == Severity::Error) return true;
  return false;
}

std::string wall_time_utc() {
  const auto now = std::chrono::system_clock::now();
  const std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm{};
  gmtime_r(&t, &tm);
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return buf;
}

void DecisionLog::emit(std::ostream& os) const {
  os << "request_id=" << request_id_ << " at=" << wall_time_utc()
     << " events=" << events_.size() << "\n";
  for (const auto& e : events_) {
    os << "  [" << to_string(e.severity) << "] " << e.request_id << " "
       << e.code;
    if (!e.key_state.empty()) os << " state{" << e.key_state << "}";
    os << " :: " << e.message << "\n";
  }
}

}  // namespace chebcore::diag
