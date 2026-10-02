#include "explain/trace.h"
#include <sstream>
namespace mp::explain {
std::string format_log_line(const TraceEntry& e) {
  std::ostringstream os;
  os << "[" << e.request_id;
  if (!e.job_id.empty()) os << "/" << e.job_id;
  os << "] " << e.module << "@" << e.position << " " << e.step;
  if (!e.detail.empty()) os << " :: " << e.detail;
  return os.str();
}
} // namespace mp::explain
