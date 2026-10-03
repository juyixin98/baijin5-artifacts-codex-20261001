// Logs and reports must be correlated to the request id, show version and
// processing steps, and keep failure reasons separate from uncertainty
// conclusions.
#include "procrustes/logger.hpp"
#include "procrustes/procrustes.hpp"
#include "procrustes/report.hpp"
#include "test_macros.hpp"

#include <sstream>

using namespace procrustes;

static bool contains(const std::string& h, const std::string& n) {
  return h.find(n) != std::string::npos;
}

int main() {
  std::ostringstream sink;
  diag::Logger logger(sink, true);

  Eigen::MatrixXd p(2, 2), q(2, 2);
  p << 0, 1,
       0, 1;
  q = p;
  Eigen::VectorXd w(2);
  w << 1, 1;
  RequestContext ctx{"req-log-42"};
  FitResult r = fit({p, q, w}, FitConfig{}, ctx, &logger);
  CHECK(r.status == Status::Success);

  const std::string log_text = sink.str();
  CHECK_MSG(contains(log_text, "req=req-log-42"),
            "log must carry request id");
  CHECK_MSG(contains(log_text, kLibraryVersion),
            "log must carry library version");
  CHECK_MSG(contains(log_text, "STEP"),
            "log must show processing steps");
  CHECK_MSG(contains(log_text, "kernel"),
            "log must identify the processing component");
  CHECK(logger.records().size() >= 4);

  // Every recorded line must carry source location and request id.
  for (const auto& rec : logger.records()) {
    CHECK(rec.request_id == "req-log-42");
    CHECK(rec.line > 0);
    CHECK(!rec.file.empty());
  }

  // Failure path: separate FAIL records.
  std::ostringstream sink2;
  diag::Logger logger2(sink2, true);
  PointSet bad;
  bad.p = Eigen::MatrixXd(2, 0);
  bad.q = Eigen::MatrixXd(2, 0);
  bad.w = Eigen::VectorXd(0);
  FitResult rf = fit(bad, FitConfig{}, RequestContext{"req-fail-7"}, &logger2);
  CHECK(rf.status == Status::InvalidInput);
  CHECK_MSG(contains(sink2.str(), "FAIL"), "failure must be logged as FAIL");
  CHECK_MSG(contains(sink2.str(), "req=req-fail-7"),
            "failure log must keep request id");

  // Non-unique path: UNCERTAIN records, distinct from failures.
  std::ostringstream sink3;
  diag::Logger logger3(sink3, false);  // records but does not stream
  Eigen::MatrixXd pl(2, 3), ql(2, 3);
  pl << -1, 0, 1,
        0, 0, 0;
  ql << -2, 0, 2,
        0, 0, 0;  // scale 2 on a line
  Eigen::VectorXd wl(3);
  wl << 1, 1, 1;
  FitConfig refl;
  refl.allow_reflection = true;
  refl.estimate_scale = true;
  FitResult ru = fit({pl, ql, wl}, refl,
                     RequestContext{"req-unc-9"}, &logger3);
  CHECK(ru.status == Status::NonUniqueSolution);
  bool saw_uncertain = false;
  for (const auto& rec : logger3.records())
    if (rec.severity == diag::Severity::Uncertain) saw_uncertain = true;
  CHECK(saw_uncertain);
  CHECK(ru.diagnostics.empty());

  // Report rendering: success report shows transform + residual.
  std::string rep = diag::render_text(ctx, {p, q, w}, r, FitConfig{});
  CHECK_MSG(contains(rep, "req-log-42"), "report must show request id");
  CHECK_MSG(contains(rep, "v" + std::string(kLibraryVersion)),
            "report must show version");
  CHECK_MSG(contains(rep, "outcome      : OK"), "success must read OK");
  CHECK_MSG(contains(rep, "weighted RMS"), "report must show residual");

  // Report rendering: non-unique has an explicit uncertainty section and is
  // not classified as a hard failure.
  std::string repu = diag::render_text(RequestContext{"req-unc-9"},
                                       {pl, ql, wl}, ru, refl);
  CHECK_MSG(contains(repu, "OK_WITH_UNCERTAINTY"),
            "non-unique must be a distinct non-fatal outcome");
  CHECK_MSG(contains(repu, "UNCERTAIN / NON-UNIQUE"),
            "report must have a separate uncertainty section");
  CHECK_MSG(!contains(repu, "FAILURE REASONS"),
            "non-unique report must not list failure reasons");

  // Report rendering: hard failure only lists failure reasons.
  std::string repf = diag::render_text(RequestContext{"req-fail-7"}, bad, rf,
                                       FitConfig{});
  CHECK_MSG(contains(repf, "HARD_FAILURE"),
            "invalid input must be a hard failure");
  CHECK_MSG(contains(repf, "FAILURE REASONS"),
            "hard failure report must list reasons");

  return proc_test::finish("logging_report");
}
