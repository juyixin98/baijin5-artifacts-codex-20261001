#pragma once

// Human and machine readable interpretation of a fit result. Failure reasons
// and uncertainty/non-uniqueness conclusions are rendered in distinct
// sections so a usable-but-ambiguous answer is never mistaken for a failure.

#include "procrustes/types.hpp"

#include <iosfwd>
#include <string>

namespace procrustes::diag {

class Logger;

struct ReportOptions {
  bool verbose = false;
  int precision = 8;
};

std::string render_text(const RequestContext& ctx, const PointSet& ps,
                        const FitResult& r, const FitConfig& cfg,
                        const ReportOptions& opt = {});

// One-line status classification suitable for monitoring / exit codes.
// Success and NonUniqueSolution are non-fatal; the rest are hard failures.
const char* status_outcome(Status s);

}  // namespace procrustes::diag
