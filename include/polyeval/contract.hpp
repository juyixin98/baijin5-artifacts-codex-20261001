#pragma once
// Numeric contract: request/response types, arithmetic modes, failure
// categories and the small text request parser.
//
// The two supported modes must never be mixed inside one evaluation:
//   - FIELD (F_p): every coefficient/point is a canonical residue 0..p-1,
//     p prime (verified deterministically), outputs are residues mod p;
//   - EXACT (Z)  : arbitrary precision signed integers, exact outputs.
#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <boost/multiprecision/cpp_int.hpp>

#include "polyeval/field.hpp"

namespace polyeval::contract {

using Big = boost::multiprecision::cpp_int;

// Stable machine-readable failure codes (logs/CLI reference these strings).
enum class FailCode {
  Ok,
  ParseError,           // malformed request grammar / bad number literal
  MissingField,         // required key absent
  EmptyPoints,          // request declares zero evaluation points
  MixedMode,            // field and exact-int tokens used in one request
  BadFieldElement,      // residue outside [0,p) in FIELD mode
  BadModulus,           // p < 2
  NonPrimeModulus,      // p is composite
  TooSmallMemoryBudget, // budget cannot even hold a single-point tree
  InvalidConfigValue,   // config file value malformed
  InternalError
};

const char* fail_code_name(FailCode c) noexcept;

enum class Mode { Unset, Field, Exact };
const char* mode_name(Mode m) noexcept;

struct ParsedRequest {
  std::string id;                       // request identity preserved end-to-end
  Mode mode = Mode::Unset;
  std::optional<std::uint64_t> prime;   // field only
  std::optional<std::uint64_t> batch_size;     // explicit batch (0 => auto by budget)
  std::optional<std::uint64_t> memory_bytes;   // product-tree memory budget
  std::optional<std::uint64_t> crosscheck_bits; // exact Horner check cap
  std::vector<Big> coeff;               // ascending order
  std::vector<Big> points;              // request order, duplicates retained

  bool valid_structure() const noexcept {
    return mode != Mode::Unset &&
           (mode != Mode::Field || prime.has_value()) &&
           !coeff.empty() && !points.empty();
  }
};

struct PointResult {
  std::size_t index = 0;     // position in the request (0-based)
  Big point;                 // echo of x_i
  Big value;                 // exact integer or canonical residue
  bool exact_crosschecked = false;  // exact Horner agreed (exact mode)
  bool uncertain = false;           // e.g. crosscheck skipped above bit cap
  std::string note;
};

struct Response {
  std::string id;
  std::size_t request_index = 0;
  Mode mode = Mode::Unset;
  FailCode status = FailCode::Ok;
  std::string failure_reason;          // single, explicit failure reason
  std::optional<std::uint64_t> prime;
  std::size_t batch_size_used = 0;
  std::size_t point_count = 0;
  std::uint64_t tree_slots = 0;
  std::uint64_t multiply_scalar_ops = 0;
  std::uint64_t remainder_scalar_ops = 0;
  std::vector<PointResult> results;    // exactly request order
};

struct ParseFailure {
  std::string id;      // request id when known, else ""
  std::size_t request_index = 0;
  FailCode code = FailCode::ParseError;
  std::string message; // includes line number / offending token
};

struct ParseOutcome {
  std::vector<ParsedRequest> requests;
  std::vector<ParseFailure> failures;  // per-request, never aborts whole file
};

// Parse the request text format documented in README. One blank line or a
// "request <id>" line starts a new request.
ParseOutcome parse_requests(const std::string& text);

Big parse_signed_big(const std::string& s);

}  // namespace polyeval::contract
