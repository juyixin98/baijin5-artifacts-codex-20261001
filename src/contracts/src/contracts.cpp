#include "procrustes/contracts.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace procrustes {

const char* status_name(Status s) noexcept {
  switch (s) {
    case Status::Success: return "Success";
    case Status::InvalidInput: return "InvalidInput";
    case Status::NonUniqueSolution: return "NonUniqueSolution";
    case Status::ScaleNotIdentifiable: return "ScaleNotIdentifiable";
  }
  return "Unknown";
}

}  // namespace procrustes

namespace procrustes::contracts {

ValidationReport validate_input(const PointSet& ps) {
  ValidationReport rep;
  const auto fail = [&](const std::string& why) {
    rep.ok = false;
    rep.status = Status::InvalidInput;
    rep.reasons.push_back(why);
  };

  if (ps.p.cols() == 0) {
    fail("point set is empty (need n >= 1 paired points)");
    return rep;
  }
  if (ps.p.rows() != ps.q.rows())
    fail("source/target dimension mismatch: p is " +
         std::to_string(ps.p.rows()) + "-d, q is " +
         std::to_string(ps.q.rows()) + "-d");
  if (ps.p.cols() != ps.q.cols())
    fail("source/target count mismatch: " + std::to_string(ps.p.cols()) +
         " source points vs " + std::to_string(ps.q.cols()) +
         " target points");
  if (ps.w.size() != ps.p.cols())
    fail("weight count (" + std::to_string(ps.w.size()) +
         ") does not match point count (" + std::to_string(ps.p.cols()) +
         ")");
  if (ps.p.rows() < 1) fail("spatial dimension must be >= 1");
  if (ps.w.size() == ps.p.cols() && (ps.w.array() < 0.0).any())
    fail("all weights must be non-negative");
  if (!ps.p.allFinite()) fail("source points contain non-finite values");
  if (!ps.q.allFinite()) fail("target points contain non-finite values");
  if (!ps.w.allFinite()) fail("weights contain non-finite values");
  if (rep.ok) {
    const double wsum = ps.w.sum();
    if (!(wsum > 0.0))
      fail("total weight must be strictly positive (got " +
           std::to_string(wsum) + ")");
  }
  return rep;
}

double weighted_rms(const PointSet& ps, const Eigen::MatrixXd& R,
                    const Eigen::VectorXd& t, double scale) {
  const int d = static_cast<int>(ps.p.rows());
  const int n = static_cast<int>(ps.p.cols());
  if (R.rows() != d || R.cols() != d || t.size() != d ||
      ps.q.rows() != d || ps.q.cols() != n || ps.w.size() != n)
    throw std::invalid_argument("weighted_rms: dimension mismatch");
  if (!(scale >= 0.0))
    throw std::invalid_argument("weighted_rms: negative scale");
  const double wsum = ps.w.sum();
  if (!(wsum > 0.0))
    throw std::invalid_argument("weighted_rms: total weight not positive");
  const Eigen::MatrixXd model = (scale * R * ps.p).colwise() + t;
  const Eigen::VectorXd per_point =
      (ps.q - model).colwise().squaredNorm().transpose();
  return std::sqrt(ps.w.dot(per_point) / wsum);
}

PostconditionReport verify_postconditions(const PointSet& ps,
                                          const FitResult& r,
                                          const FitConfig& cfg, double tol) {
  PostconditionReport out;
  out.scale_value = r.scale;
  const int d = r.rotation.rows();

  const Eigen::MatrixXd sym =
      r.rotation.transpose() * r.rotation -
      Eigen::MatrixXd::Identity(d, d);
  out.orthogonality_error = sym.cwiseAbs().maxCoeff();
  if (out.orthogonality_error > tol)
    out.violations.push_back(
        "rotation not orthogonal: ||R^T R - I||_inf = " +
        std::to_string(out.orthogonality_error) + " > " +
        std::to_string(tol));

  out.determinant = r.rotation.determinant();
  // When reflections are allowed only |det R| = 1 is required; in
  // rotation-only mode it must be +1.
  if (cfg.allow_reflection) {
    out.determinant_error = std::abs(std::abs(out.determinant) - 1.0);
  } else {
    out.determinant_error = std::abs(out.determinant - 1.0);
  }
  if (out.determinant_error > tol)
    out.violations.push_back(
        cfg.allow_reflection
            ? "|det(R)| differs from 1: det(R) = " +
                  std::to_string(out.determinant)
            : "rotation-only mode requires det(R) = +1, got det(R) = " +
                  std::to_string(out.determinant));

  if (cfg.estimate_scale && !(r.scale > 0.0))
    out.violations.push_back("similarity scale must be positive, got " +
                             std::to_string(r.scale));

  out.rms_crosscheck =
      weighted_rms(ps, r.rotation, r.translation, r.scale);
  out.rms_discrepancy = std::abs(out.rms_crosscheck - r.rms);
  if (out.rms_discrepancy > 1e-8 * std::max(1.0, out.rms_crosscheck))
    out.violations.push_back(
        "RMS residual mismatch: kernel reported " + std::to_string(r.rms) +
        ", contract recomputed " + std::to_string(out.rms_crosscheck));

  out.ok = out.violations.empty();
  return out;
}

}  // namespace procrustes::contracts
