// Reflection mode vs rotation-only mode, explicitly separated.
// The reference transform is a hand-written reflection matrix
// F = diag(1, -1) (mirror across the x-axis), det(F) = -1.
#include "procrustes/procrustes.hpp"
#include "test_macros.hpp"

using namespace procrustes;

int main() {
  Eigen::MatrixXd p(2, 4);
  p << 0, 1, 1, 0,
       0, 0, 1, 1;
  Eigen::MatrixXd F(2, 2);  // hand-written reflection, not kernel output
  F << 1,  0,
       0, -1;
  Eigen::VectorXd t(2);
  t << 3.0, 5.0;
  Eigen::VectorXd w(4);
  w << 1, 1, 2, 2;
  const Eigen::MatrixXd q = (F * p).colwise() + t;

  CHECK_CLOSE(F.determinant(), -1.0, 1e-15);

  // Rotation-only fit cannot represent a mirror: det forced to +1 and the
  // residual is strictly positive (a rotation best approximates the mirror).
  FitConfig rot_only;
  rot_only.allow_reflection = false;
  FitResult rr = fit({p, q, w}, rot_only, RequestContext{"ut-ref-rot"});
  CHECK(rr.status == Status::Success);
  CHECK_CLOSE(rr.rotation.determinant(), 1.0, 1e-12);
  CHECK_MSG(rr.rms > 0.1,
            "rotation-only fit of reflected data must leave residual");
  CHECK_CLOSE((rr.rotation - F).cwiseAbs().maxCoeff() > 0.5, true, 1e-12);

  // Allowing reflection recovers the exact mirror with zero residual.
  FitConfig refl;
  refl.allow_reflection = true;
  FitResult rf = fit({p, q, w}, refl, RequestContext{"ut-ref-all"});
  CHECK(rf.status == Status::Success);
  CHECK_CLOSE(rf.rotation.determinant(), -1.0, 1e-11);
  CHECK_CLOSE((rf.rotation - F).cwiseAbs().maxCoeff(), 0.0, 1e-11);
  CHECK_CLOSE((rf.translation - t).cwiseAbs().maxCoeff(), 0.0, 1e-11);
  CHECK_CLOSE(rf.rms, 0.0, 1e-12);
  auto post = contracts::verify_postconditions({p, q, w}, rf, refl);
  CHECK_MSG(post.ok, "reflection-fit postconditions must hold");

  // A plain rotation must still be recovered even when reflections are
  // permitted (minimizer prefers det +1 for non-reflected data).
  Eigen::MatrixXd R(2, 2);
  R << 0, -1, 1, 0;
  Eigen::MatrixXd qr = (R * p).colwise() + t;
  FitResult rf2 = fit({p, qr, w}, refl);
  CHECK_CLOSE(rf2.rotation.determinant(), 1.0, 1e-11);
  CHECK_CLOSE((rf2.rotation - R).cwiseAbs().maxCoeff(), 0.0, 1e-11);
  CHECK_CLOSE(rf2.rms, 0.0, 1e-12);

  return proc_test::finish("reflection_modes");
}
