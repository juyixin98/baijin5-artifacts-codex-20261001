#include "procrustes/logger.hpp"

#include <iomanip>
#include <sstream>

namespace procrustes::diag {

const char* severity_name(Severity s) {
  switch (s) {
    case Severity::Step: return "STEP";
    case Severity::Info: return "INFO";
    case Severity::Warn: return "WARN";
    case Severity::Fail: return "FAIL";
    case Severity::Uncertain: return "UNCERTAIN";
  }
  return "?";
}

std::string Logger::format(const LogRecord& r) {
  std::ostringstream ss;
  ss << "[req=" << r.request_id << "]"
     << "[v" << r.version << "]"
     << "[" << severity_name(r.severity) << "]"
     << "[" << r.component << "] " << r.message
     << "  @ " << r.file << ":" << r.line;
  return ss.str();
}

}  // namespace procrustes::diag
