#pragma once

// Numeric contracts: preconditions on paired weighted point sets and
// postconditions on fitted transforms. No fitting logic lives here.

#include "procrustes/types.hpp"

namespace procrustes::contracts {

struct ValidationReport {
  bool ok = true;
  Status status = Status::Success;
  std::vector<std::string> reasons;
};

// Preconditions: matching dimensions/counts, finite values, non-negative
// weights and strictly positive total weight.
ValidationReport validate_input(const PointSet& ps);

// Weighted root-mean-square residual, independently recomputed from the
// reported transform. Throws std::invalid_argument on malformed matrices.
double weighted_rms(const PointSet& ps, const Eigen::MatrixXd& R,
                    const Eigen::VectorXd& t, double scale);

struct PostconditionReport {
  bool ok = false;
  double orthogonality_error = 0.0;  // ||R^T R - I||_inf
  double determinant = 0.0;          // det(R)
  double determinant_error = 0.0;    // |det(R) - expected sign|
  double rms_crosscheck = 0.0;       // RMS recomputed by the contracts module
  double rms_discrepancy = 0.0;      // |reported - recomputed|
  double scale_value = 1.0;
  std::vector<std::string> violations;
};

// Postconditions: R orthogonal; det(R) = +1 in rotation-only mode, |det R| = 1
// when reflection is allowed; RMS cross-check matches the reported value;
// similarity scale must be positive.
PostconditionReport verify_postconditions(const PointSet& ps,
                                          const FitResult& r,
                                          const FitConfig& cfg,
                                          double tol = 1e-9);

}  // namespace procrustes::contracts
