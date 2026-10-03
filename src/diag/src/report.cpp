#include "procrustes/report.hpp"

#include <iomanip>
#include <sstream>

namespace procrustes::diag {

const char* status_outcome(Status s) {
  switch (s) {
    case Status::Success: return "OK";
    case Status::NonUniqueSolution: return "OK_WITH_UNCERTAINTY";
    case Status::InvalidInput: return "HARD_FAILURE";
    case Status::ScaleNotIdentifiable: return "HARD_FAILURE";
  }
  return "UNKNOWN";
}

static std::string mode_line(const FitConfig& cfg) {
  std::string m = cfg.estimate_scale ? "similarity" : "rigid";
  m += cfg.allow_reflection ? " (reflection allowed)"
                            : " (rotation-only, det=+1)";
  return m;
}

std::string render_text(const RequestContext& ctx, const PointSet& ps,
                        const FitResult& r, const FitConfig& cfg,
                        const ReportOptions& opt) {
  std::ostringstream ss;
  ss << std::setprecision(opt.precision);
  ss << "==== Procrustes fit report v" << kLibraryVersion << " ====\n";
  ss << "request_id   : " << (ctx.request_id.empty() ? "-" : ctx.request_id)
     << "\n";
  ss << "mode         : " << mode_line(cfg) << "\n";
  ss << "outcome      : " << status_outcome(r.status)
     << " (status=" << status_name(r.status) << ")\n";
  ss << "points       : d=" << r.dimension << " n=" << r.point_count
     << " total_weight=" << r.total_weight << "\n";

  if (r.status == Status::InvalidInput) {
    ss << "\n-- FAILURE REASONS --\n";
    for (const auto& m : r.diagnostics) ss << "  ! " << m << "\n";
    return ss.str();
  }

  ss << "covariance   : rank=" << r.rank << "/" << r.dimension
     << " singular_values=[";
  for (int i = 0; i < r.singular_values.size(); ++i)
    ss << (i ? ", " : "") << r.singular_values(i);
  ss << "]\n";
  ss << "spread       : source=" << r.source_spread
     << " target=" << r.target_spread << "\n";
  ss << "scale        : " << r.scale << "\n";
  ss << "rotation R   :\n" << r.rotation << "\n";
  ss << "translation t: " << r.translation.transpose() << "\n";
  ss << "weighted RMS residual : " << r.rms << "\n";
  ss << "weighted residual sum : " << r.weighted_residual_sum << "\n";

  if (!r.uncertainties.empty()) {
    ss << "\n-- UNCERTAIN / NON-UNIQUE CONCLUSIONS --\n";
    for (const auto& u : r.uncertainties) ss << "  ? " << u << "\n";
  }
  if (!r.diagnostics.empty()) {
    ss << "\n-- FAILURE REASONS --\n";
    for (const auto& m : r.diagnostics) ss << "  ! " << m << "\n";
  }
  if (!r.warnings.empty()) {
    ss << "\n-- WARNINGS --\n";
    for (auto w : r.warnings)
      ss << "  ~ "
         << (w == WarningCode::NearDegenerateGeometry
                 ? "near-degenerate geometry (smallest non-zero singular "
                   "value is tiny)"
                 : "warning")
         << "\n";
  }
  if (opt.verbose) {
    ss << "\n-- INPUT SUMMARY --\n";
    ss << "source centroid : " << r.source_centroid.transpose() << "\n";
    ss << "target centroid : " << r.target_centroid.transpose() << "\n";
    ss << "p (first up to 5 cols):\n"
       << ps.p.leftCols(std::min<int>(5, ps.p.cols())) << "\n";
  }
  ss << "============================\n";
  return ss.str();
}

}  // namespace procrustes::diag
