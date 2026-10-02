#include "explain/report.h"
#include "mp/version.h"
#include <sstream>

namespace mp::explain {
using contract::Fail;

std::string escape_json(const std::string& s) {
  std::string out;
  out.reserve(s.size() + 2);
  for (char c : s) {
    switch (c) {
    case '"': out += "\\\""; break;
    case '\\': out += "\\\\"; break;
    case '\n': out += "\\n"; break;
    case '\r': out += "\\r"; break;
    case '\t': out += "\\t"; break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        char buf[8];
        std::snprintf(buf, sizeof(buf), "\\u%04x", c);
        out += buf;
      } else out += c;
    }
  }
  return out;
}

namespace {
const char* outcome(const JobReport& j) {
  if (j.failure) return "FAILURE";
  if (!j.uncertainties.empty()) return "UNCERTAIN";
  return "OK";
}
} // namespace

std::string render_text(const RequestReport& r, bool steps) {
  std::ostringstream os;
  os << "VERSION multipoint-eval " << r.version
     << " request=" << r.request_id << "\n";
  for (const auto& f : r.request_failures) {
    os << "FAILURE request=" << f.request_id
       << " code=" << contract::fail_code(f.code)
       << " at=" << f.location << " :: " << f.detail << "\n";
  }
  for (const auto& j : r.jobs) {
    os << "JOB request=" << j.request_id << " id=" << j.job_id
       << " domain=" << j.domain;
    if (!j.modulus.empty()) os << " mod=" << j.modulus;
    os << " points=" << j.point_count << " batches=" << j.batches
       << " batch_cap=" << j.batch_cap
       << " tree_bytes=" << j.tree_bytes_estimated << "/" << j.tree_bytes_limit
       << " order_preserved=" << (j.order_preserved ? "true" : "false")
       << " outcome=" << outcome(j) << "\n";
    if (j.failure) {
      os << "  FAILURE code=" << contract::fail_code(j.failure.code)
         << " at=" << j.failure.location << " :: " << j.failure.detail << "\n";
    }
    for (const auto& u : j.uncertainties)
      os << "  UNCERTAIN code=" << u.code << " at=" << u.where
         << " :: " << u.detail << "\n";
    for (const auto& p : j.results)
      os << "  RESULT idx=" << p.index << " x=" << p.point
         << " p(x)=" << p.value << "\n";
  }
  if (steps && r.trace) {
    for (const auto& e : r.trace->entries())
      os << "STEP " << format_log_line(e) << "\n";
  }
  os << "END\n";
  return os.str();
}

std::string render_json(const RequestReport& r, bool steps) {
  std::ostringstream os;
  os << "{\n";
  os << "  \"version\": \"" << MP_VERSION_STRING << "\",\n";
  os << "  \"request\": \"" << escape_json(r.request_id) << "\",\n";
  os << "  \"request_failures\": [";
  for (size_t i = 0; i < r.request_failures.size(); ++i) {
    const auto& f = r.request_failures[i];
    os << (i ? ", " : "") << "{\"code\": \"" << contract::fail_code(f.code)
       << "\", \"at\": \"" << escape_json(f.location)
       << "\", \"detail\": \"" << escape_json(f.detail) << "\"}";
  }
  os << "],\n  \"jobs\": [\n";
  for (size_t a = 0; a < r.jobs.size(); ++a) {
    const auto& j = r.jobs[a];
    os << "    {\n";
    os << "      \"request\": \"" << escape_json(j.request_id) << "\",\n";
    os << "      \"id\": \"" << escape_json(j.job_id) << "\",\n";
    os << "      \"domain\": \"" << j.domain << "\",\n";
    os << "      \"modulus\": \"" << escape_json(j.modulus) << "\",\n";
    os << "      \"points\": " << j.point_count << ",\n";
    os << "      \"batches\": " << j.batches << ",\n";
    os << "      \"batch_cap\": " << j.batch_cap << ",\n";
    os << "      \"tree_bytes_estimated\": " << j.tree_bytes_estimated << ",\n";
    os << "      \"tree_bytes_limit\": " << j.tree_bytes_limit << ",\n";
    os << "      \"order_preserved\": " << (j.order_preserved ? "true" : "false") << ",\n";
    os << "      \"outcome\": \"" << outcome(j) << "\",\n";
    if (j.failure) {
      os << "      \"failure\": {\"code\": \"" << contract::fail_code(j.failure.code)
         << "\", \"at\": \"" << escape_json(j.failure.location)
         << "\", \"detail\": \"" << escape_json(j.failure.detail) << "\"},\n";
    }
    os << "      \"uncertainties\": [";
    for (size_t i = 0; i < j.uncertainties.size(); ++i) {
      const auto& u = j.uncertainties[i];
      os << (i ? ", " : "") << "{\"code\": \"" << escape_json(u.code)
         << "\", \"at\": \"" << escape_json(u.where)
         << "\", \"detail\": \"" << escape_json(u.detail) << "\"}";
    }
    os << "],\n      \"results\": [";
    for (size_t i = 0; i < j.results.size(); ++i) {
      const auto& p = j.results[i];
      os << (i ? ", " : "")
         << "{\"idx\": " << p.index << ", \"x\": \"" << escape_json(p.point)
         << "\", \"value\": \"" << escape_json(p.value) << "\"}";
    }
    os << "]\n    }" << (a + 1 == r.jobs.size() ? "" : ",") << "\n";
  }
  os << "  ]";
  if (steps && r.trace) {
    os << ",\n  \"steps\": [";
    const auto& es = r.trace->entries();
    for (size_t i = 0; i < es.size(); ++i) {
      const auto& e = es[i];
      os << (i ? ", " : "") << "{\"request\": \"" << escape_json(e.request_id)
         << "\", \"job\": \"" << escape_json(e.job_id)
         << "\", \"module\": \"" << e.module
         << "\", \"at\": \"" << escape_json(e.position)
         << "\", \"step\": \"" << escape_json(e.step)
         << "\", \"detail\": \"" << escape_json(e.detail) << "\"}";
    }
    os << "]";
  }
  os << "\n}\n";
  return os.str();
}
} // namespace mp::explain
