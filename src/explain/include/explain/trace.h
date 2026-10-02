#pragma once
// Interpretable execution trace. Every step carries the processing location
// (file basename + line), module and request/job identity, so a log line can
// always be traced back to the originating request and code position.
#include <string>
#include <vector>

namespace mp::explain {

struct TraceEntry {
  std::string request_id;
  std::string job_id;
  std::string module;    // numcontract | core | app | polybench
  std::string position;  // basename.cpp:line
  std::string step;      // short machine-ish key, e.g. "batch.planned"
  std::string detail;    // human-readable parameters/values
};

class Trace {
public:
  void set_identity(std::string request_id, std::string job_id = "") {
    request_id_ = std::move(request_id);
    job_id_ = std::move(job_id);
  }
  void set_job(std::string job_id) { job_id_ = std::move(job_id); }

  void add(std::string module, std::string position,
           std::string step, std::string detail) {
    entries_.push_back({request_id_, job_id_, module, position,
                        std::move(step), std::move(detail)});
  }

  const std::vector<TraceEntry>& entries() const noexcept { return entries_; }

private:
  std::string request_id_;
  std::string job_id_;
  std::vector<TraceEntry> entries_;
};

// Compact single-line log form: [request/job] module@position step detail
std::string format_log_line(const TraceEntry& e);

} // namespace mp::explain

#define MP_HERE_CORE __FILE__ ":"
#define MP_TRACE(trace, module, step, detail)                                  \
  do {                                                                         \
    (trace).add((module),                                                      \
                (std::string(__FILE__).substr(                                 \
                    std::string(__FILE__).find_last_of('/') + 1) +             \
                    ":" + std::to_string(__LINE__)),                           \
                (step), (detail));                                             \
  } while (0)
