#include "procrustes/contract.hpp"

#include <cmath>

namespace procrustes {

const char* toString(TransformMode mode) {
  switch (mode) {
    case TransformMode::OrthogonalProper:
      return "orthogonal-proper";
    case TransformMode::OrthogonalAllowReflection:
      return "orthogonal-allow-reflection";
    case TransformMode::SimilarityProper:
      return "similarity-proper";
    case TransformMode::SimilarityAllowReflection:
      return "similarity-allow-reflection";
  }
  return "unknown";
}

const char* toString(FailureCode code) {
  switch (code) {
    case FailureCode::None:
      return "none";
    case FailureCode::EmptyInput:
      return "empty-input";
    case FailureCode::SizeMismatch:
      return "size-mismatch";
    case FailureCode::DimensionMismatch:
      return "dimension-mismatch";
    case FailureCode::NegativeWeight:
      return "negative-weight";
    case FailureCode::TotalWeightZero:
      return "total-weight-zero";
    case FailureCode::NaNOrInf:
      return "nan-or-inf";
    case FailureCode::SingularNonUnique:
      return "singular-non-unique";
  }
  return "unknown";
}

const char* toString(Severity severity) {
  switch (severity) {
    case Severity::Info:
      return "info";
    case Severity::Warning:
      return "warning";
    case Severity::Error:
      return "error";
  }
  return "unknown";
}

bool parseMode(const std::string& text, TransformMode& mode) {
  if (text == "rigid" || text == "orthogonal" ||
      text == "orthogonal-proper" || text == "rigid-proper") {
    mode = TransformMode::OrthogonalProper;
  } else if (text == "rigid-reflect" || text == "orthogonal-allow-reflection") {
    mode = TransformMode::OrthogonalAllowReflection;
  } else if (text == "similarity" || text == "similarity-proper") {
    mode = TransformMode::SimilarityProper;
  } else if (text == "similarity-reflect" ||
             text == "similarity-allow-reflection") {
    mode = TransformMode::SimilarityAllowReflection;
  } else {
    return false;
  }
  return true;
}

namespace {

Diagnostic makeError(FailureCode code, const std::string& step,
                     std::string message, std::string detail = "") {
  return Diagnostic{Severity::Error, code, step, std::move(message),
                    std::move(detail)};
}

bool finiteMatrix(const Eigen::MatrixXd& m) {
  return m.allFinite();
}

}  // namespace

std::vector<Diagnostic> validateRequest(const Request& request) {
  std::vector<Diagnostic> errors;
  const int n = static_cast<int>(request.source.cols());
  const int d = static_cast<int>(request.source.rows());

  if (n == 0) {
    errors.push_back(makeError(FailureCode::EmptyInput, "validate",
                               "request contains no point pairs",
                               "source columns = 0"));
    return errors;  // nothing else is meaningful for empty input
  }

  if (request.target.cols() != n || request.weights.size() != n) {
    errors.push_back(makeError(
        FailureCode::SizeMismatch, "validate",
        "source, target and weights must have the same point count",
        "source n=" + std::to_string(request.source.cols()) +
            " target n=" + std::to_string(request.target.cols()) +
            " weights n=" + std::to_string(request.weights.size())));
  }

  if (request.target.rows() != d) {
    errors.push_back(makeError(
        FailureCode::DimensionMismatch, "validate",
        "source and target must live in the same dimensionality",
        "source d=" + std::to_string(d) +
            " target d=" + std::to_string(request.target.rows())));
  }

  if (!(request.rank_tol > 0.0) || !(request.rank_tol < 1.0) ||
      !std::isfinite(request.rank_tol)) {
    errors.push_back(makeError(
        FailureCode::NaNOrInf, "validate",
        "rank_tol must be finite and lie in (0, 1)",
        "rank_tol=" + std::to_string(request.rank_tol)));
  }

  if (!finiteMatrix(request.source) || !finiteMatrix(request.target) ||
      !request.weights.allFinite()) {
    errors.push_back(makeError(FailureCode::NaNOrInf, "validate",
                               "source, target or weights contain NaN/Inf"));
  }

  bool negative = false;
  for (int i = 0; i < request.weights.size(); ++i) {
    if (request.weights(i) < 0.0) {
      negative = true;
      break;
    }
  }
  if (negative) {
    errors.push_back(makeError(FailureCode::NegativeWeight, "validate",
                               "all weights must be non-negative"));
  } else if (request.weights.sum() <= 0.0) {
    errors.push_back(makeError(
        FailureCode::TotalWeightZero, "validate",
        "sum of weights must be strictly positive",
        "sum(w)=" + std::to_string(request.weights.sum())));
  }

  return errors;
}

}  // namespace procrustes
