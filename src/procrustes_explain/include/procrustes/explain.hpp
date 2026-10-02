#pragma once

#include "procrustes/contract.hpp"

#include <ostream>
#include <string>

namespace procrustes {

// Renders a FitResult with errors, uncertain/non-unique conclusions and
// ordinary processing steps kept in separate sections.
struct ExplainOptions {
  bool show_steps = true;
  std::string indent;
};

std::string renderText(const FitResult& result,
                       const ExplainOptions& options = {});
std::string renderJson(const FitResult& result);

void writeText(const FitResult& result, std::ostream& out,
               const ExplainOptions& options = {});

}  // namespace procrustes
