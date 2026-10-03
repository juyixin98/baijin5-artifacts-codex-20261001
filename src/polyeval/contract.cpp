#include "polyeval/contract.hpp"

#include <cctype>
#include <sstream>
#include <stdexcept>

namespace polyeval::contract {

const char* fail_code_name(FailCode c) noexcept {
  switch (c) {
    case FailCode::Ok: return "OK";
    case FailCode::ParseError: return "PARSE_ERROR";
    case FailCode::MissingField: return "MISSING_FIELD";
    case FailCode::EmptyPoints: return "EMPTY_POINTS";
    case FailCode::MixedMode: return "MIXED_MODE";
    case FailCode::BadFieldElement: return "BAD_FIELD_ELEMENT";
    case FailCode::BadModulus: return "BAD_MODULUS";
    case FailCode::NonPrimeModulus: return "NON_PRIME_MODULUS";
    case FailCode::TooSmallMemoryBudget: return "TOO_SMALL_MEMORY_BUDGET";
    case FailCode::InvalidConfigValue: return "INVALID_CONFIG_VALUE";
    case FailCode::InternalError: return "INTERNAL_ERROR";
  }
  return "UNKNOWN";
}

const char* mode_name(Mode m) noexcept {
  switch (m) {
    case Mode::Field: return "FIELD";
    case Mode::Exact: return "EXACT";
    case Mode::Unset: return "UNSET";
  }
  return "UNSET";
}

Big parse_signed_big(const std::string& s) {
  Big v;
  try {
    v = Big(s);
  } catch (const std::exception&) {
    throw std::invalid_argument("invalid integer literal: '" + s + "'");
  }
  return v;
}

namespace {

std::string trim(const std::string& s) {
  std::size_t a = 0, b = s.size();
  while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
  return s.substr(a, b - a);
}

std::vector<std::string> split_ws(const std::string& s) {
  std::vector<std::string> out;
  std::istringstream is(s);
  std::string tok;
  while (is >> tok) out.push_back(tok);
  return out;
}

// Token classification for a number in a declared mode.
// F:123 forces field interpretation; Z:-5 forces exact. Plain values follow
// the request mode (sign permitted only in exact mode).
struct NumberToken {
  bool force_field = false;
  bool force_exact = false;
  std::string literal;
};

NumberToken classify_number(const std::string& tok) {
  NumberToken nt;
  if (tok.rfind("F:", 0) == 0) { nt.force_field = true; nt.literal = tok.substr(2); }
  else if (tok.rfind("Z:", 0) == 0) { nt.force_exact = true; nt.literal = tok.substr(2); }
  else nt.literal = tok;
  return nt;
}

std::uint64_t parse_u64_strict(const std::string& s) {
  if (s.empty()) throw std::invalid_argument("empty integer");
  for (char c : s) if (!std::isdigit(static_cast<unsigned char>(c)))
      throw std::invalid_argument("not a non-negative integer: '" + s + "'");
  return field::parse_u64(s);
}

void append_numbers(std::vector<Big>& dst, const std::vector<std::string>& toks,
                    Mode mode, std::uint64_t prime, const std::string& section,
                    std::size_t lineno, ParseFailure& fail, bool& failed) {
  for (const std::string& raw : toks) {
    NumberToken nt = classify_number(raw);
    Big v;
    if (nt.force_field && mode == Mode::Exact) {
      fail.code = FailCode::MixedMode;
      fail.message = "line " + std::to_string(lineno) + ": F:-prefixed number '" +
                     raw + "' used in EXACT request (" + section + ")";
      failed = true; return;
    }
    if (nt.force_exact && mode == Mode::Field) {
      fail.code = FailCode::MixedMode;
      fail.message = "line " + std::to_string(lineno) + ": Z:-prefixed number '" +
                     raw + "' used in FIELD request (" + section + ")";
      failed = true; return;
    }
    try {
      v = parse_signed_big(nt.literal);
    } catch (const std::exception& e) {
      fail.code = FailCode::ParseError;
      fail.message = "line " + std::to_string(lineno) + ": " + e.what();
      failed = true; return;
    }
    if (mode == Mode::Field) {
      if (v < 0 || v >= Big(prime)) {
        fail.code = FailCode::BadFieldElement;
        fail.message = "line " + std::to_string(lineno) + ": field element '" + raw +
                       "' is outside canonical range [0, " + std::to_string(prime) +
                       ") (" + section + ")";
        failed = true; return;
      }
    }
    dst.push_back(std::move(v));
  }
}

}  // namespace

ParseOutcome parse_requests(const std::string& text) {
  ParseOutcome out;
  std::istringstream is(text);
  std::string line;
  std::size_t lineno = 0;
  std::size_t request_index = 0;

  struct Builder {
    ParsedRequest req;
    bool started = false;
    bool failed = false;
    ParseFailure failure;
  };
  std::optional<Builder> cur;

  auto finish = [&]() {
    if (!cur) return;
    Builder& b = *cur;
    if (b.failed) {
      out.failures.push_back(b.failure);
    } else {
      ParsedRequest& r = b.req;
      auto failf = [&](FailCode code, std::string msg) {
        ParseFailure pf{r.id, request_index, code, std::move(msg)};
        out.failures.push_back(std::move(pf));
        b.failed = true;
      };
      if (r.mode == Mode::Unset) failf(FailCode::MissingField, "no 'mode' declared");
      else if (r.mode == Mode::Field && !r.prime) failf(FailCode::MissingField, "FIELD request missing 'prime'");
      else if (r.mode == Mode::Field && !field::is_prime_u64(*r.prime))
        failf(FailCode::NonPrimeModulus, "prime is not prime: " + std::to_string(*r.prime));
      else if (r.coeff.empty()) failf(FailCode::MissingField, "no coefficients provided");
      else if (r.points.empty()) failf(FailCode::EmptyPoints, "no evaluation points provided");
      if (!b.failed) out.requests.push_back(std::move(r));
    }
    cur.reset();
  };

  while (std::getline(is, line)) {
    ++lineno;
    std::string raw_line = line;
    std::string l = trim(line);
    if (l.empty() || l[0] == '#') {
      if (l.empty()) { finish(); ++request_index; }
      continue;
    }
    std::size_t colon = l.find(':');
    std::string key = colon == std::string::npos ? l : trim(l.substr(0, colon));
    std::string val = colon == std::string::npos ? "" : trim(l.substr(colon + 1));
    std::string lower;
    lower.reserve(key.size());
    for (char c : key) lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));

    if (lower == "request" || lower == "begin") {
      finish();
      ++request_index;
      cur.emplace();
      cur->started = true;
      cur->req.id = val.empty() ? ("req-" + std::to_string(request_index)) : val;
      cur->failure.id = cur->req.id;
      cur->failure.request_index = request_index;
      continue;
    }
    if (!cur) {
      cur.emplace();
      cur->started = true;
      cur->req.id = "req-" + std::to_string(request_index ? request_index + 1 : 1);
      cur->failure.id = cur->req.id;
    }
    Builder& b = *cur;
    b.failure.request_index = request_index;
    auto markfail = [&](FailCode code, std::string msg) {
      b.failed = true; b.failure.code = code; b.failure.message = std::move(msg);
    };
    if (b.failed) continue;

    auto set_mode = [&](Mode m) {
      if (b.req.mode != Mode::Unset && b.req.mode != m) {
        markfail(FailCode::MixedMode, "line " + std::to_string(lineno) +
                    ": conflicting/duplicate mode declaration");
        return false;
      }
      b.req.mode = m; return true;
    };

    if (lower == "mode") {
      if (val == "field" || val == "F") set_mode(Mode::Field);
      else if (val == "exact" || val == "integer" || val == "Z") set_mode(Mode::Exact);
      else markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": unknown mode '" + val + "'");
    } else if (lower == "id") {
      if (!val.empty()) { b.req.id = val; b.failure.id = val; }
    } else if (lower == "prime" || lower == "modulus") {
      try { b.req.prime = parse_u64_strict(val); }
      catch (const std::exception& e) { markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": " + e.what()); }
    } else if (lower == "coeff" || lower == "coefficients" || lower == "poly") {
      if (b.req.mode == Mode::Unset) { markfail(FailCode::MissingField, "line " + std::to_string(lineno) + ": coefficients before 'mode'"); continue; }
      std::uint64_t prime = b.req.prime.value_or(0);
      append_numbers(b.req.coeff, split_ws(val), b.req.mode, prime, "coeff", lineno, b.failure, b.failed);
    } else if (lower == "points" || lower == "x") {
      if (b.req.mode == Mode::Unset) { markfail(FailCode::MissingField, "line " + std::to_string(lineno) + ": points before 'mode'"); continue; }
      std::uint64_t prime = b.req.prime.value_or(0);
      append_numbers(b.req.points, split_ws(val), b.req.mode, prime, "points", lineno, b.failure, b.failed);
    } else if (lower == "batch_size" || lower == "batch") {
      try { b.req.batch_size = parse_u64_strict(val); }
      catch (const std::exception& e) { markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": " + e.what()); }
    } else if (lower == "memory_bytes" || lower == "memory") {
      try { b.req.memory_bytes = parse_u64_strict(val); }
      catch (const std::exception& e) { markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": " + e.what()); }
    } else if (lower == "crosscheck_bits") {
      try { b.req.crosscheck_bits = parse_u64_strict(val); }
      catch (const std::exception& e) { markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": " + e.what()); }
    } else {
      markfail(FailCode::ParseError, "line " + std::to_string(lineno) + ": unknown key '" + key + "'");
    }
  }
  finish();
  return out;
}

}  // namespace polyeval::contract
