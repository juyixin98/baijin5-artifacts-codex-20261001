// Degenerate (collinear / lower-rank) point sets must receive an explicit
// non-unique diagnosis, and the collinear multiple-solution structure is
// demonstrated with a hand-built reference. The canonical SVD answer remains
// a valid minimizer (identical residual).
#include "procrustes/contracts.hpp"
#include "procrustes/procrustes.hpp"
#include "test_macros.hpp"

#include <numbers>

using namespace procrustes;

// Independently evaluate a candidate transform's weighted RMS, bypassing the
// kernel so the alternative solutions are checked from first principles.
static double independent_rms(const PointSet& ps, const Eigen::MatrixXd& R,
                              const Eigen::VectorXd& t, double s) {
  return contracts::weighted_rms(ps, R, t, s);
}

int main() {
  // =====================================================================
  // 2D collinear sources along x; hand reference is a +90 degree rigid
  // rotation plus translation, so targets lie on the y-axis (rank 1).
  // =====================================================================
  Eigen::MatrixXd p(2, 5);
  p << -2, -1, 0, 1, 2,
        0,  0, 0, 0, 0;
  Eigen::MatrixXd R90(2, 2);
  R90 << 0, -1,
         1,  0;
  Eigen::VectorXd t(2);
  t << 4, -2;
  Eigen::VectorXd w(5);
  w << 1, 2, 3, 2, 1;
  Eigen::MatrixXd q = (R90 * p).colwise() + t;

  // Rotation-only: a 2D rank-1 configuration still defines a unique rotation
  // (the det=-1 mirror alternative is forbidden).
  FitResult rot = fit({p, q, w}, FitConfig{}, RequestContext{"ut-col-rot"});
  CHECK(rot.status == Status::Success);
  CHECK(rot.rank == 1);
  CHECK_CLOSE((rot.rotation - R90).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE(rot.rotation.determinant(), 1.0, 1e-11);
  CHECK_CLOSE(rot.rms, 0.0, 1e-11);
  CHECK(rot.uncertainties.empty());

  // Reflections allowed: the mirror across the line of points is a SECOND
  // exact solution, so the fit must be diagnosed as non-unique.
  FitConfig refl;
  refl.allow_reflection = true;
  FitResult refr = fit({p, q, w}, refl, RequestContext{"ut-col-refl"});
  CHECK(refr.status == Status::NonUniqueSolution);
  CHECK(refr.rank == 1);
  CHECK(refr.uncertainties.size() >= 1);
  CHECK_MSG(refr.uncertainties[0].find("non-unique") != std::string::npos,
            "uncertainty must explicitly say non-unique");
  // The canonical answer is still an exact minimizer.
  CHECK_CLOSE(refr.rms, 0.0, 1e-11);

  // Hand-built second minimizer: R_alt = My * R90 mirrors across the y-axis,
  // i.e. the line of TARGET points (q = R90 p + t). With p = (x, 0), the
  // target line direction is (0, 1), so the mirror leaves every paired point
  // unchanged while flipping the unconstrained source x-direction.
  Eigen::MatrixXd My(2, 2);
  My << -1, 0,
         0, 1;
  Eigen::MatrixXd R_alt = My * R90;  // independent alternative, det = -1
  CHECK_CLOSE(R_alt.determinant(), -1.0, 1e-12);
  const Eigen::VectorXd t_alt =
      (q * w) / w.sum() - R_alt * ((p * w) / w.sum());
  CHECK_CLOSE(independent_rms({p, q, w}, R90, t, 1.0), 0.0, 1e-11);
  CHECK_CLOSE(independent_rms({p, q, w}, R_alt, t_alt, 1.0), 0.0, 1e-11);
  CHECK_MSG((R_alt - R90).cwiseAbs().maxCoeff() > 0.5,
            "the two collinear solutions must be genuinely different");

  // =====================================================================
  // 3D rank-1: points and targets both on a line. Rotation is free to spin
  // around the line, so even rotation-only mode is non-unique.
  // =====================================================================
  Eigen::MatrixXd p3(3, 4);
  p3 << -3, -1, 1, 3,
         0,  0, 0, 0,
         0,  0, 0, 0;
  Eigen::MatrixXd q3(3, 4);
  q3 <<  0,  4, 8, 12,  // s = 2 along x
         0,  0, 0, 0,
         0,  0, 0, 0;
  Eigen::VectorXd w3(4);
  w3 << 1, 1, 1, 1;
  FitConfig sim3;
  sim3.estimate_scale = true;  // scale 2 IS identifiable on a line
  FitResult r3 = fit({p3, q3, w3}, sim3, RequestContext{"ut-line3d"});
  CHECK(r3.status == Status::NonUniqueSolution);
  CHECK(r3.rank == 1);
  CHECK(r3.uncertainties.size() >= 1);
  CHECK_CLOSE(r3.scale, 2.0, 1e-10);
  CHECK_CLOSE(r3.rms, 0.0, 1e-10);
  CHECK_CLOSE(std::abs(r3.rotation.determinant()), 1.0, 1e-10);

  // Show concretely that multiple rotations around x all solve it: build a
  // 45-degree spin around the line independently and confirm zero residual.
  const double a = std::numbers::pi / 4.0;
  Eigen::MatrixXd SpinX(3, 3);
  SpinX << 1, 0, 0,
           0, std::cos(a), -std::sin(a),
           0, std::sin(a),  std::cos(a);
  Eigen::MatrixXd R_canon = r3.rotation;
  const Eigen::VectorXd c3p = (p3 * w3) / w3.sum();
  const Eigen::VectorXd c3q = (q3 * w3) / w3.sum();
  const Eigen::VectorXd t_spin = c3q - 2.0 * SpinX * c3p;
  CHECK_CLOSE(independent_rms({p3, q3, w3}, SpinX, t_spin, 2.0), 0.0, 1e-10);
  CHECK_CLOSE(independent_rms({p3, q3, w3}, R_canon, r3.translation, 2.0),
              0.0, 1e-10);
  CHECK_MSG((SpinX - R_canon).cwiseAbs().maxCoeff() > 0.1,
            "spin around the line is a distinct minimizer");

  // =====================================================================
  // 3D rank-2 (planar) rotation-only: rank >= d-1 keeps uniqueness.
  // =====================================================================
  Eigen::MatrixXd p3p(3, 3);
  p3p << 1, 0, 0,
         0, 1, 0,
         0, 0, 0;
  Eigen::MatrixXd Rz(3, 3);  // +90 degree rotation about z
  Rz << 0, -1, 0,
        1,  0, 0,
        0,  0, 1;
  Eigen::VectorXd t3p(3);
  t3p << 1, 1, 1;
  Eigen::MatrixXd q3p = (Rz * p3p).colwise() + t3p;
  Eigen::VectorXd w3p(3);
  w3p << 1, 1, 1;
  FitResult r3p = fit({p3p, q3p, w3p}, FitConfig{},
                      RequestContext{"ut-plane3d"});
  CHECK(r3p.status == Status::Success);
  CHECK(r3p.rank == 2);
  CHECK(r3p.uncertainties.empty());
  CHECK_CLOSE((r3p.rotation - Rz).cwiseAbs().maxCoeff(), 0.0, 1e-10);
  CHECK_CLOSE(r3p.rms, 0.0, 1e-10);

  // Same planar data with reflection allowed becomes ambiguous (rank < d).
  FitConfig refl3;
  refl3.allow_reflection = true;
  FitResult r3pr = fit({p3p, q3p, w3p}, refl3);
  CHECK(r3pr.status == Status::NonUniqueSolution);
  CHECK_CLOSE(r3pr.rms, 0.0, 1e-10);

  return proc_test::finish("degenerate_multisolution");
}
