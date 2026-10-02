#pragma once
#include <sstream>
#include <string>

namespace pade::log {

// A run id encodes time + pid + a per-process counter so every log line can
// be correlated with a specific invocation even when many runs are interleaved.
std::string makeRunId();

// Structured single-line record: [level] run=<id> step=<step> msg
void emit(const std::string& level, const std::string& run_id,
          const std::string& step, const std::string& msg);

void info(const std::string& run_id, const std::string& step, const std::string& msg);
void warn(const std::string& run_id, const std::string& step, const std::string& msg);
void error(const std::string& run_id, const std::string& step, const std::string& msg);

// Enable/disable sink; tests keep it enabled and attach the id to assertions.
void setEnabled(bool enabled);
bool enabled();

} // namespace pade::log
