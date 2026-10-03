// Invalid inputs must fail with specific status/failure categories, never
// silently produce a transform. Also covers contract postcondition failures.
#include "procrustes/contracts.hpp"
#include "procrustes/procrustes.hpp"
#include "test_macros.hpp"

using namespace procrustes;

int main() {
  Eigen::MatrixXd p(2, 3), q(2, 3);
  p << 0, 1, 2,
       0, 1, 2;
  q = p;
  Eigen::VectorXd w(3);
  w << 1, 1, 1;

  // Empty point set.
  PointSet empty;
  empty.p = Eigen::MatrixXd(2, 0);
  empty.q = Eigen::MatrixXd(2, 0);
  empty.w = Eigen::VectorXd(0);
  FitResult re = fit(empty, FitConfig{});
  CHECK(re.status == Status::InvalidInput);
  CHECK(re.diagnostics.size() == 1);
  CHECK_MSG(re.diagnostics[0].find("empty") != std::string::npos,
            "must diagnose emptiness");

  // Dimension mismatch.
  PointSet dm;
  dm.p = p;
  dm.q = Eigen::MatrixXd(3, 3);
  dm.w = w;
  FitResult rdm = fit(dm, FitConfig{});
  CHECK(rdm.status == Status::InvalidInput);
  CHECK_MSG(rdm.diagnostics[0].find("dimension") != std::string::npos,
            "must diagnose dimension mismatch");

  // Count mismatch.
  PointSet cm;
  cm.p = p;
  cm.q = q.leftCols(2);
  cm.w = w;
  FitResult rcm = fit(cm, FitConfig{});
  CHECK(rcm.status == Status::InvalidInput);
  CHECK_MSG(rcm.diagnostics[0].find("count") != std::string::npos,
            "must diagnose count mismatch");

  // Weight count mismatch.
  PointSet wm;
  wm.p = p;
  wm.q = q;
  wm.w = Eigen::VectorXd(2);
  FitResult rwm = fit(wm, FitConfig{});
  CHECK(rwm.status == Status::InvalidInput);
  CHECK_MSG(rwm.diagnostics[0].find("weight") != std::string::npos,
            "must diagnose weight count");

  // Negative weight.
  PointSet neg;
  neg.p = p;
  neg.q = q;
  neg.w = Eigen::VectorXd(3);
  neg.w << 1, -2, 1;
  FitResult rneg = fit(neg, FitConfig{});
  CHECK(rneg.status == Status::InvalidInput);
  CHECK_MSG(rneg.diagnostics[0].find("non-negative") != std::string::npos,
            "must diagnose negative weight");

  // Zero total weight.
  PointSet zw;
  zw.p = p;
  zw.q = q;
  zw.w = Eigen::VectorXd::Zero(3);
  FitResult rzw = fit(zw, FitConfig{});
  CHECK(rzw.status == Status::InvalidInput);
  CHECK_MSG(rzw.diagnostics[0].find("total weight") != std::string::npos,
            "must diagnose zero total weight");

  // Non-finite values.
  PointSet nf;
  nf.p = p;
  nf.q = q;
  nf.q(0, 1) = std::numeric_limits<double>::infinity();
  nf.w = w;
  FitResult rnf = fit(nf, FitConfig{});
  CHECK(rnf.status == Status::InvalidInput);
  CHECK_MSG(rnf.diagnostics[0].find("non-finite") != std::string::npos,
            "must diagnose non-finite input");

  // Contract module directly: tampered rotation violates orthogonality.
  FitResult good = fit({p, q, w}, FitConfig{});
  CHECK(good.status == Status::Success);
  FitResult tampered = good;
  tampered.rotation(0, 0) += 0.5;  // break R^T R = I
  auto bad_post =
      contracts::verify_postconditions({p, q, w}, tampered, FitConfig{});
  CHECK(!bad_post.ok);
  CHECK(bad_post.orthogonality_error > 0.4);
  CHECK(bad_post.violations.size() >= 1);
  CHECK_MSG(bad_post.violations[0].find("orthogonal") != std::string::npos,
            "must name orthogonality violation");

  // Tampered reported RMS is caught by independent cross-check.
  FitResult bad_rms = good;
  bad_rms.rms = good.rms + 5.0;
  auto rms_post =
      contracts::verify_postconditions({p, q, w}, bad_rms, FitConfig{});
  CHECK(!rms_post.ok);
  CHECK_MSG(rms_post.violations.back().find("RMS") != std::string::npos,
            "must catch RMS discrepancy");

  // Rotation-only contract rejects a det=-1 matrix.
  FitResult det_bad = good;
  det_bad.rotation(0, 0) = -det_bad.rotation(0, 0);
  det_bad.rotation(0, 1) = -det_bad.rotation(0, 1);
  auto det_post =
      contracts::verify_postconditions({p, q, w}, det_bad, FitConfig{});
  CHECK(!det_post.ok);
  CHECK_MSG(det_post.violations[0].find("det") != std::string::npos,
            "must catch determinant violation");

  return proc_test::finish("input_validation");
}
