// Weighted similarity (scale + rotation/translation) fits and the scale
// identifiability condition. References are hand-written s, R, t.
#include "procrustes/procrustes.hpp"
#include "test_macros.hpp"

using namespace procrustes;

int main() {
  // Hand reference: s = 2.5, R = +90 degrees, t = (-1, 4).
  Eigen::MatrixXd p(2, 4);
  p << 0, 2, 2, 0,
       0, 0, 1, 1;
  const double s_ref = 2.5;
  Eigen::MatrixXd R(2, 2);
  R << 0, -1, 1, 0;
  Eigen::VectorXd t(2);
  t << -1.0, 4.0;
  Eigen::VectorXd w(4);
  w << 2, 1, 1, 2;
  const Eigen::MatrixXd q = (s_ref * R * p).colwise() + t;

  // Rigid mode must misfit (cannot absorb the 2.5x scale): residual > 0.
  FitResult rigid = fit({p, q, w}, FitConfig{}, RequestContext{"ut-sim-rigid"});
  CHECK(rigid.status == Status::Success);
  CHECK_CLOSE(rigid.scale, 1.0, 1e-15);
  CHECK_MSG(rigid.rms > 0.5, "rigid fit of scaled data must leave residual");

  FitConfig sim;
  sim.estimate_scale = true;
  FitResult r = fit({p, q, w}, sim, RequestContext{"ut-sim-2d"});
  CHECK(r.status == Status::Success);
  CHECK_CLOSE(r.scale, 2.5, 1e-10);
  CHECK_CLOSE((r.rotation - R).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE((r.translation - t).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE(r.rms, 0.0, 1e-10);
  CHECK_CLOSE(r.rotation.determinant(), 1.0, 1e-11);
  auto post = contracts::verify_postconditions({p, q, w}, r, sim);
  CHECK_MSG(post.ok, "similarity postconditions must hold");

  // 3D similarity: s = 0.4, 180-degree rotation about z, translation.
  Eigen::MatrixXd p3(3, 4);
  p3 << 1, -1, 2, 0,
        1,  1, 0, 2,
        0,  1, 1, 3;
  Eigen::MatrixXd R3(3, 3);
  R3 << -1, 0, 0,
         0,-1, 0,
         0, 0, 1;
  Eigen::VectorXd t3(3);
  t3 << 6, -2, 1.5;
  Eigen::VectorXd w3(4);
  w3 << 3, 1, 2, 4;
  Eigen::MatrixXd q3 = (0.4 * R3 * p3).colwise() + t3;
  FitConfig sim3;
  sim3.estimate_scale = true;
  FitResult r3 = fit({p3, q3, w3}, sim3, RequestContext{"ut-sim-3d"});
  CHECK(r3.status == Status::Success);
  CHECK_CLOSE(r3.scale, 0.4, 1e-10);
  CHECK_CLOSE((r3.rotation - R3).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE((r3.translation - t3).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE(r3.rms, 0.0, 1e-10);

  // Reflected similarity: s = 1.5 with F = diag(1, -1), reflection allowed.
  Eigen::MatrixXd F(2, 2);
  F << 1, 0, 0, -1;
  Eigen::MatrixXd qf = (1.5 * F * p).colwise() + t;
  FitConfig simref;
  simref.estimate_scale = true;
  simref.allow_reflection = true;
  FitResult rf = fit({p, qf, w}, simref, RequestContext{"ut-sim-refl"});
  CHECK(rf.status == Status::Success);
  CHECK_CLOSE(rf.scale, 1.5, 1e-9);
  CHECK_CLOSE(rf.rotation.determinant(), -1.0, 1e-10);
  CHECK_CLOSE((rf.rotation - F).cwiseAbs().maxCoeff(), 0.0, 1e-9);
  CHECK_CLOSE(rf.rms, 0.0, 1e-9);

  // Scale identifiability: coincident source points cannot determine scale.
  Eigen::MatrixXd pc = Eigen::MatrixXd::Zero(2, 3);
  pc.colwise() = Eigen::Vector2d(7, 7);  // all sources identical
  Eigen::MatrixXd qc(2, 3);
  qc << 1, 1, 1,
        2, 2, 2;  // all targets identical at (1,2)
  Eigen::VectorXd wc(3);
  wc << 1, 1, 1;
  FitConfig simc;
  simc.estimate_scale = true;
  FitResult rc = fit({pc, qc, wc}, simc, RequestContext{"ut-sim-coincide"});
  CHECK(rc.status == Status::ScaleNotIdentifiable);
  CHECK(rc.diagnostics.size() >= 1);
  CHECK_MSG(rc.diagnostics[0].find("scale") != std::string::npos,
            "diagnostic must mention scale identifiability");

  // Same coincident data in rigid mode: translation is fixed but rotation is
  // arbitrary, so the result is a usable fit with a non-unique diagnosis
  // (not a hard failure).
  FitResult rc_rigid = fit({pc, qc, wc}, FitConfig{});
  CHECK(rc_rigid.status == Status::NonUniqueSolution);
  CHECK_CLOSE(rc_rigid.translation(0), -6.0, 1e-9);
  CHECK_CLOSE(rc_rigid.translation(1), -5.0, 1e-9);
  CHECK_CLOSE(rc_rigid.rms, 0.0, 1e-9);
  CHECK(rc_rigid.uncertainties.size() >= 1);

  return proc_test::finish("similarity_scale");
}
