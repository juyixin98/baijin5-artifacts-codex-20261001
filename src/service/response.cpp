#include "response.hpp"
#include <cstdio>

namespace fft::service {

namespace {
std::string jesc(const std::string& s) {
  std::string o;
  o.reserve(s.size() + 2);
  for (char c : s) {
    switch (c) {
    case '"': o += "\\\""; break;
    case '\\': o += "\\\\"; break;
    case '\n': o += "\\n"; break;
    case '\r': o += "\\r"; break;
    case '\t': o += "\\t"; break;
    default: o += c;
    }
  }
  return o;
}
} // namespace

void Response::emitJson(std::FILE* sink, bool pretty) const {
  const char* nl = pretty ? "\n" : "";
  const char* sp = pretty ? "  " : "";
  std::fprintf(sink,
      "{%s"
      "%s\"requestId\": \"%s\",%s"
      "%s\"version\": \"%s\",%s"
      "%s\"status\": \"%s\",%s"
      "%s\"uncertain\": %s,%s"
      "%s\"kernelPath\": \"%s\",%s"
      "%s\"length\": %zu,%s"
      "%s\"convolutionLength\": %zu,%s"
      "%s\"peakWorkspaceBytes\": %zu,%s"
      "%s\"maxAbsError\": %.17g,%s"
      "%s\"roundTripError\": %.17g,%s"
      "%s\"failureReason\": \"%s\",%s"
      "%s\"detail\": \"%s\"%s"
      "}%s",
      nl,
      sp, jesc(requestId).c_str(), nl,
      sp, version.c_str(), nl,
      sp, common::statusName(status), nl,
      sp, uncertain ? "true" : "false", nl,
      sp, kernelPath.c_str(), nl,
      sp, length, nl,
      sp, convolutionLength, nl,
      sp, peakWorkspaceBytes, nl,
      sp, maxAbsError, nl,
      sp, roundTripError, nl,
      sp, jesc(failureReason).c_str(), nl,
      sp, jesc(detail).c_str(), nl,
      nl);
}

} // namespace fft::service
