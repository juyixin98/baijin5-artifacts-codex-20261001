#include "procrustes/procrustes.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>

namespace procrustes {

namespace {
namespace sv_tags {
constexpr const char* kComponent = "kernel";
}

std::string vec_preview(const Eigen::VectorXd& v) {
  std::ostringstream ss;
  ss << "[";
  for (int i = 0; i < v.size(); ++i) ss << (i ? ", " : "") << v(i);
  ss << "]";
  return ss.str();
}
}  // namespace

FitResult fit(const PointSet& ps, const FitConfig& cfg,
              const RequestContext& ctx, diag::Logger* logger) {
  FitResult out;
  const auto log = [&](diag::Severity s, const std::string& m,
                       const std::source_location loc =
                           std::source_location::current()) {
    if (logger) logger->emit(ctx, sv_tags::kComponent, s, m, loc);
  };

  // ---- Step 1: numeric contract (preconditions) ---------------------------
  const auto validation = contracts::validate_input(ps);
  if (!validation.ok) {
    out.status = Status::InvalidInput;
    out.diagnostics = validation.reasons;
    for (const auto& why : validation.reasons)
      log(diag::Severity::Fail, "precondition violated: " + why);
    return out;
  }

  const int d = static_cast<int>(ps.p.rows());
  const int n = static_cast<int>(ps.p.cols());
  const double wsum = ps.w.sum();
  out.dimension = d;
  out.point_count = n;
  out.total_weight = wsum;

  log(diag::Severity::Step,
      "input accepted: d=" + std::to_string(d) +
          " n=" + std::to_string(n) + " total_weight=" +
          std::to_string(wsum));

  // ---- Step 2: weighted centroids and centered coordinates ---------------
  const Eigen::VectorXd pbar = (ps.p * ps.w) / wsum;
  const Eigen::VectorXd qbar = (ps.q * ps.w) / wsum;
  out.source_centroid = pbar;
  out.target_centroid = qbar;
  log(diag::Severity::Step,
      "weighted centroids: pbar=" + vec_preview(pbar) +
          " qbar=" + vec_preview(qbar));

  const Eigen::MatrixXd pc = ps.p.colwise() - pbar;
  const Eigen::MatrixXd qc = ps.q.colwise() - qbar;

  // Weighted spreads alpha_p = sum w_i ||pc_i||^2, alpha_q likewise.
  const Eigen::VectorXd pc_norm2 = pc.colwise().squaredNorm().transpose();
  const Eigen::VectorXd qc_norm2 = qc.colwise().squaredNorm().transpose();
  const double alpha_p = ps.w.dot(pc_norm2);
  const double alpha_q = ps.w.dot(qc_norm2);
  out.source_spread = alpha_p;
  out.target_spread = alpha_q;
  log(diag::Severity::Step,
      "weighted spreads: source=" + std::to_string(alpha_p) +
          " target=" + std::to_string(alpha_q));

  // ---- Step 3: scale identifiability --------------------------------------
  if (cfg.estimate_scale) {
    // s is only identifiable relative to a non-vanishing source spread:
    // identical (coincident) source points pin translation only.
    const double max_pc2 = pc_norm2.maxCoeff();
    const double spread_floor = cfg.spread_tol * wsum * max_pc2;
    if (!(alpha_p > spread_floor)) {
      out.status = Status::ScaleNotIdentifiable;
      out.diagnostics.push_back(
          "scale not identifiable: weighted source spread " +
          std::to_string(alpha_p) +
          " is at or below the identifiability floor " +
          std::to_string(spread_floor) +
          " (all effectively paired source points coincide)");
      log(diag::Severity::Fail,
          "scale identifiability check failed (alpha_p too small)");
      return out;
    }
    log(diag::Severity::Step,
        "scale identifiability check passed (s will be estimated)");
  }

  // ---- Step 4: weighted cross-covariance and SVD --------------------------
  const Eigen::MatrixXd H = pc * ps.w.asDiagonal() * qc.transpose();
  Eigen::JacobiSVD<Eigen::MatrixXd> svd(
      H, Eigen::ComputeFullU | Eigen::ComputeFullV);
  const Eigen::VectorXd sv = svd.singularValues();
  out.singular_values = sv;
  const double sigma_floor = cfg.rank_tol * (sv.size() ? sv(0) : 0.0);
  int rank = 0;
  for (int i = 0; i < sv.size(); ++i)
    if (sv(i) > sigma_floor) ++rank;
  out.rank = rank;
  log(diag::Severity::Step,
      "SVD of weighted cross-covariance: rank=" + std::to_string(rank) +
          "/" + std::to_string(d) +
          " singular_values=" + vec_preview(sv));

  // ---- Step 5: rotation uniqueness diagnosis ------------------------------
  // The orthogonal Procrustes rotation is unique on the supported range iff
  //   * rotation-only mode (det = +1): rank(H) >= d - 1
  //   * reflections allowed     (|det| = 1): rank(H) == d
  const bool rotation_unique =
      cfg.allow_reflection ? (rank == d) : (rank >= d - 1);
  if (!rotation_unique) {
    out.uncertainties.push_back(
        "rotation is non-unique: covariance rank " + std::to_string(rank) +
        " in d=" + std::to_string(d) +
        (cfg.allow_reflection
             ? " with reflections allowed requires full rank " +
                   std::to_string(d)
             : " in rotation-only mode requires rank >= " +
                   std::to_string(d - 1)) +
        "; null-space directions admit many minimizers with identical "
        "residual. The reported transform is the canonical SVD minimizer.");
    log(diag::Severity::Uncertain,
        "rotation non-unique (rank=" + std::to_string(rank) + ")");
  }

  // In 2D with reflections allowed, a rank-1 configuration is also ambiguous:
  // reflection across the line of points gives a second minimizer. In
  // rotation-only mode that alternative has det = -1 and is excluded, so the
  // 2D rotation remains unique even at rank 1 (rank >= d-1 holds).

  // ---- Step 6: assemble orthogonal factor ---------------------------------
  Eigen::MatrixXd D = Eigen::MatrixXd::Identity(d, d);
  double det_vu = 1.0;
  if (!cfg.allow_reflection) {
    det_vu = (svd.matrixV() * svd.matrixU().transpose()).determinant();
    if (det_vu < 0.0) D(d - 1, d - 1) = -1.0;
  }
  const Eigen::MatrixXd R = svd.matrixV() * D * svd.matrixU().transpose();
  log(diag::Severity::Step,
      std::string("orthogonal factor assembled (") +
          (cfg.allow_reflection ? "reflection allowed" : "rotation-only") +
          ", det(VU^T)=" + std::to_string(det_vu) +
          ", det(R)=" + std::to_string(R.determinant()) + ")");

  // ---- Step 7: scale and translation --------------------------------------
  double s = 1.0;
  if (cfg.estimate_scale) {
    // s = tr(D Sigma) / alpha_p, where tr(D Sigma) is the signed correlation
    // along the SVD basis (sum of singular values, last one negated when the
    // rotation-only correction flips D).
    const double num = (sv.array() * D.diagonal().array()).sum();
    if (!(num > 0.0)) {
      out.uncertainties.push_back(
          "positive similarity scale not supported by geometry "
          "(tr(D*Sigma)=" +
          std::to_string(num) +
          "); canonical SVD scale is degenerate/zero. Data do not define a "
          "similarity fit.");
      log(diag::Severity::Uncertain,
          "non-positive scale numerator: tr(D*Sigma)=" +
              std::to_string(num));
      s = 0.0;
    } else {
      s = num / alpha_p;
    }
    log(diag::Severity::Step,
        "similarity scale estimated: s=" + std::to_string(s));
  }
  const Eigen::VectorXd t = qbar - s * R * pbar;

  out.scale = s;
  out.rotation = R;
  out.translation = t;

  // ---- Step 8: residuals --------------------------------------------------
  const Eigen::MatrixXd model = (s * R * ps.p).colwise() + t;
  const Eigen::MatrixXd diff = ps.q - model;
  const Eigen::VectorXd per_point2 =
      diff.colwise().squaredNorm().transpose();
  out.weighted_residual_sum = ps.w.dot(per_point2);
  out.rms = std::sqrt(out.weighted_residual_sum / wsum);
  log(diag::Severity::Step,
      "weighted RMS residual = " + std::to_string(out.rms));

  // ---- Step 9: soft warnings and final status -----------------------------
  if (rank > 0) {
    const double smallest_nz = sv(rank - 1);
    if (smallest_nz < 1e-7 * sv(0)) {
      out.warnings.push_back(WarningCode::NearDegenerateGeometry);
      log(diag::Severity::Warn,
          "near-degenerate geometry: smallest retained singular value " +
              std::to_string(smallest_nz) + " vs largest " +
              std::to_string(sv(0)));
    }
  }

  if (!rotation_unique) out.status = Status::NonUniqueSolution;
  log(diag::Severity::Info,
      std::string("fit complete: status=") + status_name(out.status) +
          " rms=" + std::to_string(out.rms));
  return out;
}

}  // namespace procrustes
