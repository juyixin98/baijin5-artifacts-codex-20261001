#pragma once

// Core numeric types shared by the contracts, kernel and diagnostics modules.

#include <Eigen/Dense>
#include <string>
#include <vector>

namespace procrustes {

inline constexpr const char* kLibraryVersion = "1.0.0";

// Outcome of a fit attempt. Failure-like statuses carry machine-stable codes
// and human readable diagnostics; non-unique geometry is reported separately
// from hard failures (see FitResult::uncertainties).
enum class Status {
  Success = 0,
  InvalidInput = 1,
  NonUniqueSolution = 2,
  ScaleNotIdentifiable = 3
};

enum class WarningCode {
  NearDegenerateGeometry  // smallest nonzero singular value is very small
};

// Identifies a caller request so every log line and report can be correlated.
struct RequestContext {
  std::string request_id;
};

struct FitConfig {
  bool allow_reflection = false;  // false: rotation-only (det R = +1)
  bool estimate_scale = false;    // false: rigid (s = 1); true: similarity
  double rank_tol = 1e-10;        // singular value cutoff relative to sigma_max
  double spread_tol = 1e-12;      // point-spread cutoff relative to scale
};

struct PointSet {
  Eigen::MatrixXd p;  // d x n source points
  Eigen::MatrixXd q;  // d x n target points
  Eigen::VectorXd w;  // n non-negative weights
};

struct FitResult {
  Status status = Status::Success;
  Eigen::MatrixXd rotation;          // orthogonal d x d matrix R
  Eigen::VectorXd translation;       // t, with model q' = s R p + t
  double scale = 1.0;                // similarity scale, 1 for rigid fits
  double rms = 0.0;                  // sqrt(sum w_i ||res_i||^2 / sum w_i)
  double weighted_residual_sum = 0.0;
  int rank = -1;                     // numerical rank of weighted covariance
  int dimension = 0;
  int point_count = 0;
  double total_weight = 0.0;
  double source_spread = 0.0;        // sum w_i ||p_i - pbar||^2
  double target_spread = 0.0;
  Eigen::VectorXd singular_values;
  Eigen::VectorXd source_centroid;
  Eigen::VectorXd target_centroid;
  std::vector<std::string> diagnostics;    // hard failure reasons
  std::vector<std::string> uncertainties;  // non-fatal / non-unique conclusions
  std::vector<WarningCode> warnings;
};

const char* status_name(Status s) noexcept;

}  // namespace procrustes
