#pragma once

// Request-correlated structured logging. Every record carries the request id,
// library version, source location and a step/severity so processing can be
// reconstructed from logs alone.

#include "procrustes/types.hpp"

#include <functional>
#include <iostream>
#include <mutex>
#include <ostream>
#include <sstream>
#include <source_location>
#include <string>
#include <vector>

namespace procrustes::diag {

enum class Severity { Step, Info, Warn, Fail, Uncertain };

const char* severity_name(Severity s);

struct LogRecord {
  std::string request_id;
  std::string version;
  std::string component;
  std::string message;
  Severity severity = Severity::Info;
  std::string file;
  unsigned int line = 0;
};

class Logger {
 public:
  explicit Logger(std::ostream& os = std::cerr, bool enabled = true)
      : os_(os), enabled_(enabled) {}

  void emit(const RequestContext& ctx, const std::string& component,
            Severity sev, const std::string& msg,
            const std::source_location& loc =
                std::source_location::current()) {
    LogRecord rec{ctx.request_id.empty() ? "-" : ctx.request_id,
                  kLibraryVersion, component, msg, sev,
                  loc.file_name(), loc.line()};
    std::lock_guard<std::mutex> lock(mu_);
    records_.push_back(rec);
    if (enabled_) os_ << format(rec) << "\n";
  }

  const std::vector<LogRecord>& records() const { return records_; }
  void set_enabled(bool e) { enabled_ = e; }

  static std::string format(const LogRecord& r);

 private:
  std::ostream& os_;
  bool enabled_;
  std::mutex mu_;
  std::vector<LogRecord> records_;
};

}  // namespace procrustes::diag
