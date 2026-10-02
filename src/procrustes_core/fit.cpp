#include "procrustes/fit.hpp"

#include "procrustes/version.hpp"

#include <Eigen/SVD>

#include <cmath>
#include <sstream>
#include <string>

namespace procrustes {
namespace {

std::string vecToString(const Eigen::VectorXd& v) {
  std::ostringstream os;
  os << "[";
  for (int i = 0; i < v.size(); ++i) {
    os << (i == 0 ? "" : ", ") << v(i);
  }
  os << "]";
  return os.str();
}

Diagnostic diag(Severity severity, FailureCode code, std::string step,
                std::string message, std::string detail = "") {
  return Diagnostic{severity, code, std::move(step), std::move(message),
                    std::move(detail)};
}

}  // namespace

FitResult fit(const Request& request) {
  FitResult result;
  result.request_id = request.request_id;
  result.version = kVersionString;
  result.mode = request.mode;
  result.dimension = static_cast<int>(request.source.rows());
  result.point_count = static_cast<int>(request.source.cols());

  // --- Stage 1: numerical contract ----------------------------------------
  auto errors = validateRequest(request);
  for (auto& e : errors) {
    result.diagnostics.push_back(std::move(e));
  }
  if (!errors.empty()) {
    result.ok = false;
    result.code = errors.front().code;
    result.diagnostics.push_back(
        diag(Severity::Info, FailureCode::None, "abort",
             "fit aborted before solving because the request failed validation",
             "request_id=" + request.request_id));
    return result;
  }

  const int n = request.source.cols();
  const int d = request.source.rows();
  const bool allowReflection =
      request.mode == TransformMode::OrthogonalAllowReflection ||
      request.mode == TransformMode::SimilarityAllowReflection;
  const bool fitScale =
      request.mode == TransformMode::SimilarityProper ||
      request.mode == TransformMode::SimilarityAllowReflection;

  // --- Stage 2: weighted centroids ----------------------------------------
  const double total_w = request.weights.sum();
  const Eigen::VectorXd sqrt_w = request.weights.cwiseSqrt();
  // Weighted means: pbar = sum w p / sum w.
  const Eigen::VectorXd pbar =
      (request.source.array().rowwise() * request.weights.transpose().array())
          .rowwise()
          .sum() /
      total_w;
  const Eigen::VectorXd qbar =
      (request.target.array().rowwise() * request.weights.transpose().array())
          .rowwise()
          .sum() /
      total_w;
  result.diagnostics.push_back(diag(
      Severity::Info, FailureCode::None, "centroids",
      "computed weighted source/target centroids and total weight",
      "n=" + std::to_string(n) + " d=" + std::to_string(d) +
          " sum_w=" + std::to_string(total_w)));

  // Centred points scaled by sqrt(weight): X_i = sqrt(w_i)(p_i - pbar).
  Eigen::MatrixXd X =
      (request.source.colwise() - pbar).array().rowwise() *
      sqrt_w.transpose().array();
  Eigen::MatrixXd Y =
      (request.target.colwise() - qbar).array().rowwise() *
      sqrt_w.transpose().array();

  // var_p = sum w ||p - pbar||^2 / sum w ; var_q likewise.
  const double var_p = X.squaredNorm() / total_w;
  const double var_q = Y.squaredNorm() / total_w;

  // --- Stage 3: cross-covariance and SVD ----------------------------------
  const Eigen::MatrixXd H = X * Y.transpose();
  Eigen::JacobiSVD<Eigen::MatrixXd> svd(
      H, Eigen::ComputeFullU | Eigen::ComputeFullV);
  const Eigen::VectorXd sigma = svd.singularValues();
  const Eigen::MatrixXd U = svd.matrixU();
  const Eigen::MatrixXd V = svd.matrixV();
  result.diagnostics.push_back(diag(
      Severity::Info, FailureCode::None, "svd",
      "formed weighted cross-covariance and computed singular values",
      "singular_values=" + vecToString(sigma)));

  const double sigma_max = sigma.size() > 0 ? sigma(0) : 0.0;
  const double tol = request.rank_tol * std::max(1.0, sigma_max);
  int rank = 0;
  for (int i = 0; i < sigma.size(); ++i) {
    if (sigma(i) > tol) ++rank;
  }

  // --- Stage 4: rotation / sign correction --------------------------------
  Eigen::MatrixXd D = Eigen::MatrixXd::Identity(d, d);
  const double raw_det = (V * U.transpose()).determinant();
  if (!allowReflection && raw_det < 0.0) {
    D(d - 1, d - 1) = -1.0;
  }
  Eigen::MatrixXd R = V * D * U.transpose();
  const double det_R = R.determinant();

  result.diagnostics.push_back(diag(
      Severity::Info, FailureCode::None, "rotation",
      "assembled orthogonal factor with sign correction",
      std::string("det(VU^T)=") + std::to_string(raw_det) +
          " mode=" + toString(request.mode) +
          " sign_corrected=" + (D(d - 1, d - 1) < 0 ? "yes" : "no")));

  // --- Stage 5: scale identifiability -------------------------------------
  // Similarity scale is identifiable iff the weighted source cloud is not
  // concentrated at its centroid (var_p > 0 relative to its own magnitude).
  const double var_scale =
      request.rank_tol * std::max(1.0, var_p);
  const bool source_coincident = var_p <= var_scale;
  if (fitScale) {
    result.scale_identifiable = !source_coincident;
    if (source_coincident) {
      result.diagnostics.push_back(diag(
          Severity::Warning, FailureCode::SingularNonUnique,
          "scale-identifiability",
          "similarity scale is not identifiable: all weighted source points "
          "coincide with their centroid",
          "var_p=" + std::to_string(var_p) +
              " var_q=" + std::to_string(var_q)));
    }
  } else {
    result.scale_identifiable = true;  // fixed by mode
  }

  // --- Stage 6: scale and translation --------------------------------------
  double s = 1.0;
  if (fitScale) {
    if (source_coincident) {
      s = 0.0;  // no information; any scale yields the same fit
    } else {
      // Sum of singular values after sign correction.
      double sigma_sum = 0.0;
      for (int i = 0; i < d; ++i) {
        sigma_sum += D(i, i) * sigma(i);
      }
      s = sigma_sum / X.squaredNorm();
    }
  }
  const Eigen::VectorXd t = qbar - s * R * pbar;
  result.diagnostics.push_back(diag(
      Severity::Info, FailureCode::None, "solution",
      "computed scale and translation from weighted centroids",
      std::string("s=") + std::to_string(s) +
          " det(R)=" + std::to_string(det_R)));

  // --- Stage 7: uniqueness of the rotation --------------------------------
  bool rotation_unique = true;
  std::string nonunique_reason;
  if (rank == 0) {
    rotation_unique = false;
    nonunique_reason =
        "weighted cross-covariance has rank 0: rotation is undetermined";
  } else if (allowReflection) {
    // With reflections allowed, only rank d fixes an orthogonal map uniquely;
    // any rotation in the null space leaves the residual unchanged.
    if (rank < d) {
      rotation_unique = false;
      nonunique_reason =
          "reflection-allowed orthogonal map is non-unique for a "
          "rank-deficient point cloud (rotation freedom in the null space)";
    }
  } else {
    // Proper rotations: in 2D a rank-1 cloud still fixes the unique rotation
    // that maps one oriented line onto another; in general a codimension
    // greater than one leaves multiple proper rotations.
    if (!(d == 2 && rank == 1) && rank < d - 1) {
      rotation_unique = false;
      nonunique_reason =
          "proper rotation is non-unique: the point cloud leaves a rotation "
          "null space of dimension >= 2";
    }
  }
  if (!rotation_unique) {
    result.diagnostics.push_back(diag(
        Severity::Warning, FailureCode::SingularNonUnique,
        "rotation-uniqueness",
        nonunique_reason,
        "rank=" + std::to_string(rank) + " d=" + std::to_string(d) +
            " singular_values=" + vecToString(sigma)));
  }

  // --- Stage 8: residuals --------------------------------------------------
  const Eigen::MatrixXd fitted =
      (s * R * request.source).colwise() + t;
  const Eigen::MatrixXd residuals = request.target - fitted;
  result.residual_norms = residuals.colwise().norm().transpose();
  result.sse = request.weights.dot(result.residual_norms.array().square().matrix());
  result.rmse = std::sqrt(result.sse / total_w);
  result.max_abs_residual = result.residual_norms.size()
                               ? result.residual_norms.maxCoeff()
                               : 0.0;

  // --- Stage 9: assemble result -------------------------------------------
  result.ok = true;
  result.code = FailureCode::None;
  result.rotation = R;
  result.scale = s;
  result.translation = t;
  result.determinant = det_R;
  result.rotation_unique = rotation_unique;

  // Determinant invariant is itself a checked contract.
  if (!allowReflection && std::abs(det_R - 1.0) > 1e-9) {
    result.diagnostics.push_back(diag(
        Severity::Error, FailureCode::SingularNonUnique, "det-check",
        "proper rotation determinant differs from +1",
        "det(R)=" + std::to_string(det_R)));
  }

  return result;
}

}  // namespace procrustes
