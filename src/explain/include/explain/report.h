#pragma once
#include "explain/trace.h"
#include "numcontract/failure.h"
#include <cstdint>
#include <string>
#include <vector>

namespace mp::explain {
using contract::Failure;

// A single per-point value with its request-relative identity.
struct PointResult {
  size_t index;      // 0-based index in requested order (stable identity)
  std::string point; // original point literal
  std::string value; // evaluated value literal (canonical form)
};

// Uncertainty/qualification is kept separate from definite results and from
// failures, per the interpretability contract.
struct Uncertainty {
  std::string code;    // e.g. "FP_REL_TOLERANCE_EXCEEDED"
  std::string detail;  // what was observed vs expected
  std::string where;   // request/job/position
};

// One job's structured outcome. A job may carry definite results plus
// independent uncertainty notes, or a terminal failure (results then empty).
struct JobReport {
  std::string request_id;
  std::string job_id;
  std::string domain;        // INTEGER | FIELD
  std::string modulus;       // empty for INTEGER
  size_t batch_cap{0};  // maximum points allowed in one batch
  size_t point_count{0};
  size_t batches{0};
  uint64_t tree_bytes_estimated{0};
  uint64_t tree_bytes_limit{0};
  bool order_preserved{true};
  std::vector<PointResult> results;
  Failure failure;
  std::vector<Uncertainty> uncertainties;
};

struct RequestReport {
  std::string request_id;
  std::string version;
  std::vector<JobReport> jobs;
  std::vector<Failure> request_failures; // grammar/uniqueness errors
  const Trace* trace{nullptr};
};

// Deterministic, line-oriented text report. Sections:
//   RESULT (definite), UNCERTAIN (qualifications), FAILURE (terminal),
//   STEP (key processing steps), VERSION.
std::string render_text(const RequestReport& r, bool include_steps);

// Machine-readable JSON for independent assertions/tooling.
std::string render_json(const RequestReport& r, bool include_steps);

std::string escape_json(const std::string& s);

} // namespace mp::explain
