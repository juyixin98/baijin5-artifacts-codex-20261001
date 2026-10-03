#pragma once
// Orchestration: turns parsed requests into responses using the kernel,
// applying the memory-budgeted batching and the independent exact Horner
// cross-check. Cross-request modes may differ; within a request they cannot.
#include <string>
#include <vector>

#include "polyeval/config.hpp"
#include "polyeval/contract.hpp"
#include "polyeval/explainer.hpp"
#include "polyeval/log.hpp"

namespace polyeval::driver {

struct RunReport {
  std::vector<contract::Response> responses;  // request order, one per valid request
  std::vector<contract::ParseFailure> parse_failures;
};

RunReport run(const contract::ParseOutcome& parsed, const config::EvalConfig& cfg,
              log::JsonlLogger& logger);

// Independent, deliberately simple point-wise Horner over exact integers.
contract::Big horner_exact(const std::vector<contract::Big>& coeff,
                           const contract::Big& x);

// Serialise one response to a single JSON object line (order preserved).
std::string response_to_json(const contract::Response& r,
                             const std::vector<explainer::PointExplanation>& explanations);
std::string failure_to_json(const contract::ParseFailure& f);

}  // namespace polyeval::driver
