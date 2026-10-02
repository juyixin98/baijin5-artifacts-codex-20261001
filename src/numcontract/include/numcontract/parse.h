#pragma once
#include "numcontract/config.h"
#include "numcontract/failure.h"
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace mp::contract {

// Exactly one algebraic domain per job. INTEGER (exact, unbounded) and FIELD
// (prime modulus) must never be mixed within a job; mixing is a contract
// violation and reported as DomainMismatch.
enum class Domain { Undeclared, Integer, Field };

const char* domain_name(Domain d) noexcept;

struct Job {
  std::string id;
  int start_line{0};

  // Declaration state. The parser tracks both to detect contradictions:
  //   INTEGER with a modulus, or FIELD without one, is a DomainMismatch.
  Domain domain{Domain::Undeclared};
  std::optional<uint64_t> modulus;

  // Highest-degree-first coefficient literals, exactly as authored.
  std::vector<std::string> coeff_tokens;
  bool has_coeff_line{false};
  int coeff_line{0};

  // Evaluation point literals in requested order (duplicates preserved).
  std::vector<std::string> point_tokens;
  bool has_points_line{false};
  int points_line{0};
};

struct Request {
  std::string id{"default"};
  std::vector<Job> jobs;
};

// Strict, line-based grammar:
//   REQUEST <id>
//   JOB <id>
//   DOMAIN INTEGER|FIELD
//   MOD <decimal prime>      (required and only valid for FIELD)
//   COEFF c_k c_{k-1} ... c_0
//   POINTS x1 x2 ...          (duplicates are legal and order is significant)
// Blank lines and `#` comments are ignored.
struct ParseResult {
  Request request;
  std::vector<Failure> failures; // request-level (grammar, uniqueness, domain)
  bool ok() const noexcept { return failures.empty(); }
};

ParseResult parse_request(const std::string& text);

// Semantic validation shared by parser callers: modulus primality/range and
// per-job memory feasibility against the resolved configuration. The parser
// already covers grammar + per-job domain consistency; this covers value-level
// contract properties that depend on Config.
Failure validate_job(const Job& job, const Config& cfg) noexcept;

} // namespace mp::contract
