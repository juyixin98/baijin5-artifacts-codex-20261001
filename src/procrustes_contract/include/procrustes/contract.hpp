#pragma once

#include <Eigen/Dense>

#include <string>
#include <vector>

namespace procrustes {

// Fit modes. Reflection and rotation-only (proper) handling are deliberately
// separate, and orthogonal-only vs similarity (uniform scale) are separate.
enum class TransformMode {
  OrthogonalProper,          // det(R) == +1, scale fixed at 1
  OrthogonalAllowReflection, // det(R) == +/-1, scale fixed at 1
  SimilarityProper,          // det(R) == +1, uniform scale fitted
  SimilarityAllowReflection  // det(R) == +/-1, uniform scale fitted
};

// Machine-readable failure / uncertainty categories.
enum class FailureCode {
  None,
  EmptyInput,          // no point pairs supplied
  SizeMismatch,        // source/target/weights disagree in point count
  DimensionMismatch,   // source and target dimensionality differ
  NegativeWeight,      // a weight is below zero
  TotalWeightZero,     // weights are all zero (nothing is observed)
  NaNOrInf,            // non-finite value in the request
  SingularNonUnique    // degenerate geometry: result is not unique
};

enum class Severity { Info, Warning, Error };

const char* toString(TransformMode mode);
const char* toString(FailureCode code);
const char* toString(Severity severity);

// Parses one of: rigid | rigid-reflect | similarity | similarity-reflect.
// Returns false (and leaves mode untouched) for unknown tokens.
bool parseMode(const std::string& text, TransformMode& mode);

struct Request {
  std::string request_id;                 // correlation identity
  Eigen::MatrixXd source;                 // d x n, source points p_i (columns)
  Eigen::MatrixXd target;                 // d x n, target points q_i (columns)
  Eigen::VectorXd weights;                // n, non-negative, sum > 0
  TransformMode mode = TransformMode::OrthogonalProper;
  double rank_tol = 1e-12;                // relative singular-value cut-off
};

struct Diagnostic {
  Severity severity = Severity::Info;
  FailureCode code = FailureCode::None;
  std::string step;       // processing stage that produced the record
  std::string message;    // human-readable explanation
  std::string detail;     // extra numbers, e.g. singular values
};

struct FitResult {
  bool ok = false;
  FailureCode code = FailureCode::None;  // hard failure code, None on success
  std::string request_id;
  std::string version;

  TransformMode mode = TransformMode::OrthogonalProper;
  int dimension = 0;
  int point_count = 0;

  // Fitted map: q ~= scale * R * p + translation
  Eigen::MatrixXd rotation;              // d x d orthogonal
  double scale = 1.0;
  Eigen::VectorXd translation;           // d

  // Quality: residual_i = q_i - (s R p_i + t).
  Eigen::VectorXd residual_norms;        // per-point ||residual_i||
  double sse = 0.0;                     // sum_i w_i ||residual_i||^2
  double rmse = 0.0;                    // sqrt(sse / sum w_i)
  double max_abs_residual = 0.0;

  // Identifiability / uniqueness conclusions.
  bool rotation_unique = true;
  bool scale_identifiable = true;
  double determinant = 0.0;

  std::vector<Diagnostic> diagnostics;   // steps, warnings and errors
};

// Numerical contract validation. Returns Error diagnostics (possibly several);
// an empty vector means the request is well posed.
std::vector<Diagnostic> validateRequest(const Request& request);

}  // namespace procrustes
