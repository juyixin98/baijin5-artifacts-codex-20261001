#pragma once
// Error/uncertainty explainer. Field outputs are exact residues but may wrap;
// exact outputs are error-free big integers, yet the independent Horner
// cross-check may be skipped when values exceed a configured bit cap. The
// explainer keeps definite values separate from uncertain conclusions.
#include <cstdint>
#include <string>
#include <vector>

#include "polyeval/contract.hpp"

namespace polyeval::explainer {

struct PointExplanation {
  std::size_t index = 0;
  std::string point;
  std::string value;
  std::string status;        // EXACT | FIELD_RESIDUE | UNCERTAIN
  std::string interpretation;
  bool uncertain = false;
};

struct ExplainSummary {
  std::string id;
  std::string mode;
  std::vector<PointExplanation> points;
  std::vector<std::string> uncertainty;  // explicitly separated
  std::vector<std::string> failure;      // explicitly separated
};

std::size_t bit_length(const contract::Big& v);

PointExplanation explain_point(const contract::PointResult& pr, contract::Mode mode,
                               std::uint64_t prime);

}  // namespace polyeval::explainer
