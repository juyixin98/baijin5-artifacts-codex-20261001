#include "polyeval/log.hpp"

#include <chrono>
#include <ctime>

#include "polyeval/json.hpp"

namespace polyeval::log {
namespace {
std::string iso_now() {
  using namespace std::chrono;
  auto now = system_clock::now();
  std::time_t t = system_clock::to_time_t(now);
  std::tm tm{};
  localtime_r(&t, &tm);
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &tm);
  return buf;
}
}  // namespace

JsonlLogger::JsonlLogger(const std::string& path) {
  if (!path.empty()) out_.open(path, std::ios::out | std::ios::trunc);
}

void JsonlLogger::emit(const Event& ev) {
  if (!out_.is_open()) return;
  ++seq_;
  out_ << "{"
       << json::field("ts", json::quote(iso_now()))
       << json::field("seq", std::to_string(seq_))
       << json::field("version", json::quote("1.0.0"))
       << json::field("request_id", json::quote(ev.request_id))
       << json::field("phase", json::quote(ev.phase))
       << json::field("location", json::quote(ev.location))
       << json::field("status", json::quote(ev.status))
       << json::field("detail", json::quote(ev.detail), false)
       << "}\n";
}

void JsonlLogger::emit(std::string request_id, std::string phase, std::string location,
                       std::string detail, std::string status) {
  emit(Event{std::move(request_id), std::move(phase), std::move(location),
             std::move(detail), std::move(status)});
}

}  // namespace polyeval::log
