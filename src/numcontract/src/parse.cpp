#include "numcontract/parse.h"
#include "numcontract/primality.h"
#include <cctype>
#include <charconv>
#include <optional>
#include <sstream>

namespace mp::contract {

const char* domain_name(Domain d) noexcept {
  switch (d) {
  case Domain::Undeclared: return "UNDECLARED";
  case Domain::Integer: return "INTEGER";
  case Domain::Field: return "FIELD";
  }
  return "?";
}

namespace {
std::string trim(std::string s) {
  auto a = s.find_first_not_of(" \t\r\n");
  auto b = s.find_last_not_of(" \t\r\n");
  if (a == std::string::npos) return "";
  return s.substr(a, b - a + 1);
}
std::vector<std::string> words(const std::string& line) {
  std::vector<std::string> out;
  std::istringstream ss(line);
  std::string tok;
  while (ss >> tok) out.push_back(tok);
  return out;
}
bool parse_u64(const std::string& tok, uint64_t& out) noexcept {
  if (tok.empty()) return false;
  size_t i = (tok[0] == '+') ? 1 : 0;
  if (i == tok.size()) return false;
  for (size_t k = i; k < tok.size(); ++k)
    if (!isdigit(static_cast<unsigned char>(tok[k]))) return false;
  // Parse with explicit overflow detection (from_chars overflow is signalled
  // by errc::result_out_of_range, which a bare success check would miss).
  auto [ptr, ec] = std::from_chars(tok.data() + i,
                                   tok.data() + tok.size(), out);
  return ec == std::errc{} && ptr == tok.data() + tok.size();
}

// Structural integer-literal check shared by FIELD and INTEGER. The kernel
// performs the actual bounded (mod p) or unbounded (cpp_int) conversion.
bool integer_literal(const std::string& tok) noexcept {
  if (tok.empty()) return false;
  size_t i = 0;
  if (tok[0] == '+' || tok[0] == '-') i = 1;
  if (i == tok.size()) return false;
  for (; i < tok.size(); ++i)
    if (!isdigit(static_cast<unsigned char>(tok[i]))) return false;
  return true;
}

// Analytic smallest possible tree footprint for a one-point batch: two linear
// polynomials and a constant remainder. Kept in the contract layer so the
// feasibility failure is decided independently of kernel allocations.
uint64_t minimum_footprint_bytes(const Config& cfg) noexcept {
  // Symbolic floor for a one-point batch: two scalars (constant remainder and
  // the linear factor constant), times the configured per-slot cost. The
  // kernel planner applies the full analytic tree estimate on top.
  double est = 2.0 * cfg.bytes_per_slot;
  return static_cast<uint64_t>(est) + 64ull;
}
} // namespace

Failure validate_job(const Job& job, const Config& cfg) noexcept {
  auto fail = [&](Fail c, std::string detail) -> Failure {
    return {c, std::move(detail), "job '" + job.id + "'", ""};
  };
  if (job.domain == Domain::Undeclared)
    return fail(Fail::MalformedRequest, "job does not declare a DOMAIN");
  if (!job.has_coeff_line)
    return fail(Fail::EmptyCoefficients, "job has no COEFF line");
  if (job.coeff_tokens.empty())
    return fail(Fail::MalformedCoefficient, "COEFF must list at least one token");
  for (const auto& t : job.coeff_tokens)
    if (!integer_literal(t))
      return fail(Fail::MalformedCoefficient, "token '" + t + "' is not an integer");
  for (const auto& t : job.point_tokens)
    if (!integer_literal(t))
      return fail(Fail::MalformedPoint, "token '" + t + "' is not an integer");

  if (job.domain == Domain::Field) {
    if (!job.modulus)
      return fail(Fail::InvalidModulus, "FIELD job requires a MOD prime");
    uint64_t p = *job.modulus;
    if (p < 2) return fail(Fail::InvalidModulus, "MOD must be at least 2");
    // Range is checked before primality so oversized-but-composite moduli are
    // classified by the actual contract violation (representation overflow).
    if (p >= (1ull << 63))
      return fail(Fail::ModulusTooLarge,
                  "MOD " + std::to_string(p) + " is not smaller than 2^63");
    if (!is_prime(p))
      return fail(Fail::NonPrimeModulus, "MOD " + std::to_string(p) + " is composite");
  } else if (job.domain == Domain::Integer && job.modulus) {
    return fail(Fail::DomainMismatch,
                "INTEGER job must not declare a MOD; exact and field arithmetic cannot be mixed");
  }

  if (minimum_footprint_bytes(cfg) > cfg.memory_limit_bytes)
    return fail(Fail::InfeasibleBatchLimit,
                "memory limit is below the smallest one-point tree footprint");
  return {};
}

ParseResult parse_request(const std::string& text) {
  ParseResult result;
  auto& req = result.request;
  auto& errs = result.failures;
  auto add = [&](Fail c, std::string detail, int line) {
    errs.push_back({c, std::move(detail), "request:line " + std::to_string(line), req.id});
  };

  Job* cur = nullptr;
  std::istringstream ss(text);
  std::string raw;
  int line_no = 0;
  while (std::getline(ss, raw)) {
    ++line_no;
    std::string line = trim(raw);
    if (line.empty() || line[0] == '#') continue;
    auto toks = words(line);
    const std::string& key = toks[0];

    if (key == "REQUEST") {
      if (toks.size() != 2) { add(Fail::MalformedRequest, "REQUEST requires exactly one id", line_no); continue; }
      req.id = toks[1];
      continue;
    }
    if (key == "JOB") {
      if (toks.size() != 2) { add(Fail::MalformedRequest, "JOB requires exactly one id", line_no); continue; }
      for (const auto& j : req.jobs)
        if (j.id == toks[1]) {
          errs.push_back({Fail::DuplicateJobId, "duplicate JOB id '" + toks[1] + "'",
                          "request:line " + std::to_string(line_no), req.id});
        }
      req.jobs.push_back(Job{});
      cur = &req.jobs.back();
      cur->id = toks[1];
      cur->start_line = line_no;
      continue;
    }

    if (!cur) { add(Fail::MalformedRequest, "'" + key + "' appears before any JOB", line_no); continue; }
    auto loc = [&](std::string d) { return "job '" + cur->id + "': " + std::move(d); };

    if (key == "DOMAIN") {
      if (toks.size() != 2) { add(Fail::MalformedRequest, loc("DOMAIN requires INTEGER or FIELD"), line_no); continue; }
      Domain d = Domain::Undeclared;
      if (toks[1] == "INTEGER") d = Domain::Integer;
      else if (toks[1] == "FIELD") d = Domain::Field;
      else { add(Fail::MalformedRequest, loc("unknown DOMAIN '" + toks[1] + "'"), line_no); continue; }
      if (cur->domain != Domain::Undeclared && cur->domain != d) {
        add(Fail::DomainMismatch, loc("domain redeclared as " + toks[1]), line_no);
      }
      cur->domain = d;
    } else if (key == "MOD") {
      if (toks.size() != 2) { add(Fail::MalformedRequest, loc("MOD requires one value"), line_no); continue; }
      uint64_t p = 0;
      if (!parse_u64(toks[1], p) || p == 0) { add(Fail::InvalidModulus, loc("MOD '" + toks[1] + "' is not a positive integer"), line_no); continue; }
      if (cur->modulus && *cur->modulus != p)
        add(Fail::DomainMismatch, loc("MOD redeclared with a different value"), line_no);
      if (cur->domain == Domain::Integer)
        add(Fail::DomainMismatch, loc("MOD declared on an INTEGER job"), line_no);
      if (cur->domain == Domain::Undeclared) cur->domain = Domain::Field;
      cur->modulus = p;
    } else if (key == "COEFF") {
      if (cur->has_coeff_line) { add(Fail::MalformedRequest, loc("COEFF declared twice"), line_no); continue; }
      cur->has_coeff_line = true;
      cur->coeff_line = line_no;
      for (size_t i = 1; i < toks.size(); ++i) {
        if (!integer_literal(toks[i]))
          add(Fail::MalformedCoefficient, loc("token '" + toks[i] + "' is not an integer"), line_no);
        cur->coeff_tokens.push_back(toks[i]);
      }
    } else if (key == "POINTS") {
      if (cur->has_points_line) { add(Fail::MalformedRequest, loc("POINTS declared twice"), line_no); continue; }
      cur->has_points_line = true;
      cur->points_line = line_no;
      for (size_t i = 1; i < toks.size(); ++i) {
        if (!integer_literal(toks[i]))
          add(Fail::MalformedPoint, loc("token '" + toks[i] + "' is not an integer"), line_no);
        cur->point_tokens.push_back(toks[i]);
      }
    } else {
      add(Fail::MalformedRequest, loc("unknown directive '" + key + "'"), line_no);
    }
  }
  return result;
}
} // namespace mp::contract
