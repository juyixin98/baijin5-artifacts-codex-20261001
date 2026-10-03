// Weighted rigid (rotation-only) fits against hand-constructed references:
// a 2D 90-degree transform on the unit square with non-uniform weights and a
// 3D 120-degree axis-angle transform. Reference matrices are written out
// explicitly, never produced by the kernel under test.
#include "procrustes/procrustes.hpp"
#include "test_macros.hpp"

#include <numbers>

using namespace procrustes;

static Eigen::MatrixXd rotation_z(double theta) {
  Eigen::MatrixXd R(3, 3);
  const double c = std::cos(theta), s = std::sin(theta);
  R << c, -s, 0,
       s,  c, 0,
       0,  0, 1;
  return R;
}

int main() {
  // ---- 2D square, 90 degree rotation + translation, uneven weights -------
  Eigen::MatrixXd p(2, 4);
  p << 0, 1, 1, 0,
       0, 0, 1, 1;
  Eigen::MatrixXd R2(2, 2);
  R2 << 0, -1,
        1,  0;
  Eigen::VectorXd t2(2);
  t2 << 2.0, -3.0;
  Eigen::VectorXd w(4);
  w << 1, 2, 3, 4;
  Eigen::MatrixXd q = (R2 * p).colwise() + t2;

  FitResult r = fit({p, q, w}, FitConfig{}, RequestContext{"ut-rigid-2d"});
  CHECK(r.status == Status::Success);
  CHECK(r.rank == 2);
  CHECK_CLOSE(r.rotation.determinant(), 1.0, 1e-12);
  CHECK_CLOSE(r.rotation(0, 0), 0, 1e-12);
  CHECK_CLOSE(r.rotation(0, 1), -1, 1e-12);
  CHECK_CLOSE(r.rotation(1, 0), 1, 1e-12);
  CHECK_CLOSE(r.rotation(1, 1), 0, 1e-12);
  CHECK_CLOSE(r.translation(0), 2.0, 1e-12);
  CHECK_CLOSE(r.translation(1), -3.0, 1e-12);
  CHECK_CLOSE(r.scale, 1.0, 1e-12);
  CHECK_CLOSE(r.rms, 0.0, 1e-12);

  // Independent contract cross-check.
  auto post = contracts::verify_postconditions({p, q, w}, r, FitConfig{});
  CHECK_MSG(post.ok, "2D postconditions must hold");

  // ---- 3D 120 degree rotation about z, translation, uneven weights -------
  Eigen::MatrixXd p3(3, 5);
  p3 << 1, 0, 1, 2, -1,
        0, 1, 2, 1,  3,
        0, 0, 1, 2,  1;
  Eigen::MatrixXd R3 = rotation_z(2.0 * std::numbers::pi / 3.0);
  Eigen::VectorXd t3(3);
  t3 << -4.0, 7.0, 2.5;
  Eigen::VectorXd w3(5);
  w3 << 0.5, 2, 1.5, 3, 1;
  Eigen::MatrixXd q3 = (R3 * p3).colwise() + t3;

  FitResult r3 = fit({p3, q3, w3}, FitConfig{}, RequestContext{"ut-rigid-3d"});
  CHECK(r3.status == Status::Success);
  CHECK(r3.rank == 3);
  CHECK_CLOSE((r3.rotation - R3).cwiseAbs().maxCoeff(), 0.0, 1e-11);
  CHECK_CLOSE((r3.translation - t3).cwiseAbs().maxCoeff(), 0.0, 1e-11);
  CHECK_CLOSE(r3.rotation.determinant(), 1.0, 1e-11);
  CHECK_CLOSE(r3.rms, 0.0, 1e-10);
  auto post3 = contracts::verify_postconditions({p3, q3, w3}, r3, FitConfig{});
  CHECK_MSG(post3.ok, "3D postconditions must hold");

  // Zero-weight points must be ignored, not counted against the fit: first
  // point gets an arbitrary unrelated target but weight zero.
  Eigen::MatrixXd q3b = q3;
  q3b.col(0) << 100, -100, 50;
  Eigen::VectorXd w3b = w3;
  w3b(0) = 0.0;
  FitResult r3b = fit({p3, q3b, w3b}, FitConfig{});
  CHECK(r3b.status == Status::Success);
  CHECK_CLOSE((r3b.rotation - R3).cwiseAbs().maxCoeff(), 0.0, 1e-10);

  return proc_test::finish("rigid_2d_3d");
}
