#pragma once
// Kernel entry point: one contract-validated job -> ordered values, with
// batching driven by the memory planner. The caller (app layer) has already
// enforced the domain contract; here INTEGER and FIELD never share digits.
#include "explain/report.h"
#include "explain/trace.h"
#include "numcontract/parse.h"
#include <string>

namespace mp::core {

// Evaluates one validated job. Never throws on well-formed contract input;
// any unexpected condition is reported as Fail::InternalError.
explain::JobReport evaluate_job(const contract::Job& job,
                                const contract::Config& cfg,
                                const std::string& request_id,
                                explain::Trace& trace);

// Independent pointwise Horner reference for cross-checking a batch of string
// values. Returns "" on parse error. Lives in the kernel but is deliberately
// separate from the tree path so the cross-check is not self-referential in
// method (test-side references use a third, standalone implementation).
namespace reference {
std::string horner_integer(const std::vector<std::string>& coeff_hi_to_lo,
                           const std::string& point);
std::string horner_field(const std::vector<std::string>& coeff_hi_to_lo,
                         const std::string& point, uint64_t mod);
} // namespace reference

} // namespace mp::core
