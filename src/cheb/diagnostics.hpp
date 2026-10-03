// Diagnostics: request-scoped structured records and safe redaction.
//
// Every acceptance / rejection / indeterminate decision can emit a record
// carrying a request id and the key numerical state. Raw function samples are
// treated as potentially sensitive and are never printed verbatim: use
// redacted_sample() / redacted_vector().
#ifndef CHEB_DIAGNOSTICS_HPP
#define CHEB_DIAGNOSTICS_HPP

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <iomanip>
#include <ostream>
#include <random>
#include <cstring>
#include <sstream>
#include <string>

#include <Eigen/Dense>

#include "cheb/errors.hpp"

namespace cheb {

// Opaque correlation identifier, e.g. "req-00000000a1b2c3d4".
struct RequestId {
  std::uint64_t value = 0;
  std::string str() const {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "req-%016llx",
                  static_cast<unsigned long long>(value));
    return buf;
  }
  static RequestId generate(std::uint64_t seed_hint = 0) {
    static thread_local std::mt19937_64 rng{
        static_cast<std::uint64_t>(
            std::chrono::steady_clock::now().time_since_epoch().count()) ^
        0x9e3779b97f4a7c15ULL};
    std::uint64_t v = rng() ^ seed_hint ^ 0x9e3779b97f4a7c15ULL;
    return RequestId{v};
  }
};

// Replace a raw sample with a stable, non-reversible fingerprint plus sign and
// magnitude bucket. Useful for logs that must never expose input data.
struct RedactedSample {
  std::string fingerprint;  // first 8 hex of a 64-bit FNV-1a hash
  double magnitude_bucket = 0.0;  // floor to 1 significant decimal
  bool negative = false;
};

inline std::uint64_t fnv1a_64(const std::string& bytes) {
  std::uint64_t h = 0xcbf29ce484222325ULL;
  for (unsigned char c : bytes) {
    h ^= c;
    h *= 0x100000001b3ULL;
  }
  return h;
}

inline RedactedSample redacted_sample(double x) {
  RedactedSample r;
  r.negative = std::signbit(x);
  const double a = std::fabs(x);
  if (a == 0.0 || !std::isfinite(a)) {
    r.magnitude_bucket = a;
  } else {
    const double p10 = std::pow(10.0, std::floor(std::log10(a)));
    r.magnitude_bucket = std::floor(a / p10) * p10;
  }
  std::uint64_t bits;
  std::memcpy(&bits, &x, sizeof(bits));
  char buf[16];
  std::snprintf(buf, sizeof(buf), "%08llx",
                static_cast<unsigned long long>(fnv1a_64(
                    std::string(reinterpret_cast<const char*>(&bits),
                                sizeof(bits))) &
                    0xffffffffULL));
  r.fingerprint = buf;
  return r;
}

inline std::string redacted_vector_summary(const Eigen::VectorXd& v) {
  if (v.size() == 0) return "size=0";
  const RedactedSample first = redacted_sample(v[0]);
  const RedactedSample last = redacted_sample(v[v.size() - 1]);
  std::ostringstream os;
  os << "size=" << v.size() << " first[fp=" << first.fingerprint
     << ",mag~" << first.magnitude_bucket << "] last[fp=" << last.fingerprint
     << ",mag~" << last.magnitude_bucket << "]";
  return os.str();
}

// Minimal JSON-lines structured logger. No external dependencies.
class Logger {
 public:
  explicit Logger(std::ostream& os) : os_(os) {}

  struct Record {
    std::string request_id;
    std::string event;
    std::string verdict;
    std::string reason;
    double fit_residual = 0.0;
    double truncation_residual = 0.0;
bool have_residual = false;
    double tail_value = 0.0;
    std::string tail_kind;
    int degree = -1;
    int truncate_degree = -1;
    std::string detail;
  };

  static std::string escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 4);
    for (char c : s) {
      switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        default:
          if (static_cast<unsigned char>(c) < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof(buf), "\\u%04x",
                          static_cast<unsigned char>(c));
            out += buf;
          } else {
            out += c;
          }
      }
    }
    return out;
  }

  void emit(const Record& r) {
    std::ostringstream os;
    os << "{\"request_id\":\"" << escape(r.request_id) << "\",\"event\":\""
       << escape(r.event) << "\"";
    if (!r.verdict.empty()) os << ",\"verdict\":\"" << escape(r.verdict) << "\"";
    if (!r.reason.empty()) os << ",\"reason\":\"" << escape(r.reason) << "\"";
    if (r.have_residual)
      os << ",\"fit_residual\":" << std::setprecision(17) << r.fit_residual
         << ",\"truncation_residual\":" << r.truncation_residual;
    if (!r.tail_kind.empty())
      os << ",\"tail_kind\":\"" << escape(r.tail_kind)
         << "\",\"tail_value\":" << std::setprecision(17) << r.tail_value;
    if (r.degree >= 0) os << ",\"degree\":" << r.degree;
    if (r.truncate_degree >= 0)
      os << ",\"truncate_degree\":" << r.truncate_degree;
    if (!r.detail.empty()) os << ",\"detail\":\"" << escape(r.detail) << "\"";
    os << "}\n";
    os_ << os.str();
    os_.flush();
  }

  void decision(const std::string& request_id, const ErrorReport& rep,
                int degree, int truncate_degree) {
    Record r;
    r.request_id = request_id;
    r.event = "verdict";
    r.verdict = to_string(rep.verdict);
    r.reason = to_string(rep.reason);
    r.have_residual = rep.residual_point_count > 0;
    r.fit_residual = rep.fit_residual;
    r.truncation_residual = rep.truncation_residual;
    r.tail_kind = to_string(rep.tail.kind);
    r.tail_value = rep.tail.value;
    r.degree = degree;
    r.truncate_degree = truncate_degree;
    r.detail = rep.explanation;
    emit(r);
  }

 private:
  std::ostream& os_;
};

}  // namespace cheb

#endif
