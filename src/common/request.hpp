#pragma once
// Request-scoped identity + structured logging. Every result produced by the
// backend carries a request id, component/version and processing location so
// logs can be correlated end to end.
#include "status.hpp"
#include "version.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>
#include <sstream>
#include <string>

namespace fft::common {

struct RequestContext {
  std::string requestId;
  std::string component = BLUESTEIN_FFT_CORE_NAME;
  std::string version = BLUESTEIN_FFT_VERSION_STRING;
  std::string location; // e.g. kernel path "bluestein:L=1024"

  static std::string generateId() {
    using namespace std::chrono;
    const auto now = steady_clock::now().time_since_epoch().count();
    static std::atomic<unsigned long> seq{0};
    unsigned long s = seq.fetch_add(1);
    std::ostringstream os;
    os << "req-" << std::hex << now << "-" << s;
    return os.str();
  }
};

// One structured log line: level | request | component@version@location | msg
inline void logLine(const RequestContext& ctx, const char* level,
                    const std::string& msg, std::FILE* sink = stderr) {
  static std::mutex m;
  std::lock_guard<std::mutex> lk(m);
  std::fprintf(sink, "[%s] [%s] [%s@%s%s%s] %s\n", level,
               ctx.requestId.c_str(), ctx.component.c_str(),
               ctx.version.c_str(), ctx.location.empty() ? "" : "@",
               ctx.location.c_str(), msg.c_str());
  std::fflush(sink);
}

} // namespace fft::common
