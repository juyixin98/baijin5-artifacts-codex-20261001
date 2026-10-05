#pragma once

// Shared structured run log used by the test runner, the independent
// benchmark and the example program.
//
// Every process run gets a unique run id (UTC timestamp + pid + stream tag)
// and appends JSON-lines events to <directory>/<stream>_<run_id>.jsonl.
// Each event carries the run id, a UTC timestamp and arbitrary key/value
// fields, so a failing check can be replayed from the log alone: the test
// code records inputs, intermediate state, tolerances and the rationale for
// every judgment it makes.

#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include <unistd.h>

namespace gauss::support {

using JsonScalar = std::variant<std::string, double, long long, bool>;
using Field = std::pair<std::string, JsonScalar>;

inline std::string json_escape(std::string_view text) {
  std::string out;
  out.reserve(text.size() + 2);
  for (const char c : text) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof buf, "\\u%04x", c);
          out += buf;
        } else {
          out += c;
        }
    }
  }
  return out;
}

inline std::string scalar_to_json(const JsonScalar& value) {
  struct Visitor {
    std::string operator()(const std::string& v) const {
      return "\"" + json_escape(v) + "\"";
    }
    std::string operator()(bool v) const { return v ? "true" : "false"; }
    std::string operator()(long long v) const { return std::to_string(v); }
    std::string operator()(double v) const {
      if (!std::isfinite(v)) {
        // JSON has no inf/nan literals; keep them readable as strings.
        if (std::isnan(v)) return "\"nan\"";
        return v > 0 ? "\"inf\"" : "\"-inf\"";
      }
      std::ostringstream os;
      os.precision(17);
      os << v;
      return os.str();
    }
  };
  return std::visit(Visitor{}, value);
}

inline std::string utc_now_iso8601() {
  const std::time_t now =
      std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
  std::tm tm{};
  gmtime_r(&now, &tm);
  char buf[32];
  std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
  return buf;
}

inline std::string make_run_id(std::string_view stream_tag) {
  const std::time_t now =
      std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
  std::tm tm{};
  gmtime_r(&now, &tm);
  char stamp[32];
  std::strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%SZ", &tm);
  std::ostringstream os;
  os << stamp << "-" << ::getpid() << "-" << stream_tag;
  return os.str();
}

class RunLog {
 public:
  RunLog(std::string directory, std::string stream_name)
      : run_id_(make_run_id(stream_name)) {
    std::filesystem::create_directories(directory);
    path_ = directory + "/" + stream_name + "_" + run_id_ + ".jsonl";
    out_.open(path_, std::ios::app);
    if (!out_) {
      throw std::runtime_error("RunLog: cannot open " + path_);
    }
  }

  const std::string& run_id() const noexcept { return run_id_; }
  const std::string& path() const noexcept { return path_; }

  void event(std::initializer_list<Field> fields) {
    event(std::vector<Field>(fields));
  }

  void event(const std::vector<Field>& fields) {
    std::string line = "{\"run_id\":\"" + run_id_ + "\",\"ts\":\"" +
                       utc_now_iso8601() + "\"";
    for (const auto& [key, value] : fields) {
      line += ",\"" + json_escape(key) + "\":" + scalar_to_json(value);
    }
    line += '}';
    out_ << line << '\n';
    out_.flush();
  }

 private:
  std::string run_id_;
  std::string path_;
  std::ofstream out_;
};

}  // namespace gauss::support
