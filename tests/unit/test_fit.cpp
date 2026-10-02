#include "test_support/test_framework.hpp"

#include "procrustes/fit.hpp"

#include "procrustes/version.hpp"

#include <cmath>
#include <limits>

namespace {

using procrustes::FailureCode;
using procrustes::Request;
using procrustes::TransformMode;

constexpr double kEps = 1e-10;

// Independent 2D brute-force reference for the PROPER rigid problem:
// scan angles analytically (translation has a closed form for each angle) and
// return the minimum weighted SSE. This deliberately does not call the core.
struct RigidRef2D {
  double angle = 0.0;
  double sse = 0.0;
};

RigidRef2D bruteForceRigid2D(const Eigen::MatrixXd& p,
                             const Eigen::MatrixXd& q,
                             const Eigen::VectorXd& w,
                             int angle_steps = 200001) {
  const double total_w = w.sum();
  const Eigen::VectorXd pbar =
      ((p.array().rowwise() * w.transpose().array()).matrix().rowwise().sum()) /
      total_w;
  const Eigen::VectorXd qbar =
      ((q.array().rowwise() * w.transpose().array()).matrix().rowwise().sum()) /
      total_w;
  const Eigen::MatrixXd pc = p.colwise() - pbar;
  const Eigen::MatrixXd qc = q.colwise() - qbar;
  RigidRef2D best;
  best.sse = std::numeric_limits<double>::infinity();
  for (int k = 0; k < angle_steps; ++k) {
    const double a = 2.0 * M_PI * static_cast<double>(k) /
                     static_cast<double>(angle_steps - 1);
    Eigen::Matrix2d R;
    R << std::cos(a), -std::sin(a), std::sin(a), std::cos(a);
    double sse = 0.0;
    for (int i = 0; i < p.cols(); ++i) {
      sse += w(i) * (qc.col(i) - R * pc.col(i)).squaredNorm();
    }
    if (sse < best.sse) {
      best.sse = sse;
      best.angle = a;
    }
  }
  return best;
}

Request basicRequest(const Eigen::MatrixXd& p, const Eigen::MatrixXd& q,
                     const Eigen::VectorXd& w, TransformMode mode) {
  Request r;
  r.source = p;
  r.target = q;
  r.weights = w;
  r.mode = mode;
  r.request_id = "unit";
  return r;
}

}  // namespace

// 1. Handmade rigid transform is recovered exactly; determinant +1.
TEST_CASE("fit/rigid2d-exact-handmade-reference") {
  Eigen::MatrixXd p(2, 4);
  p << 0, 1, 0, 1,
       0, 0, 1, 1;
  Eigen::Matrix2d R;
  R << 0, -1, 1, 0;
  Eigen::Vector2d t(2, -3);
  Eigen::MatrixXd q = (R * p).colwise() + t;
  auto req = basicRequest(p, q, Eigen::VectorXd::Ones(4),
                          TransformMode::OrthogonalProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  CHECK_CLOSE(result.determinant, 1.0, 1e-12);
  CHECK_CLOSE(result.scale, 1.0, 1e-12);
  CHECK_CLOSE(result.rmse, 0.0, 1e-12);
  CHECK((result.rotation - R).cwiseAbs().maxCoeff() < kEps);
  CHECK((result.translation - t).cwiseAbs().maxCoeff() < kEps);
  CHECK(result.rotation_unique);
  CHECK_EQ_STR(result.request_id, "unit");
  CHECK_EQ_STR(result.version, procrustes::kVersionString);
}

// 2. 3D handmade similarity: scale 2, 120-degree rotation, translation.
TEST_CASE("fit/similarity3d-exact-handmade-reference") {
  const double c = -0.5;
  const double z = std::sqrt(3.0) / 2.0;
  Eigen::Matrix3d R;
  R << c, -z, 0, z, c, 0, 0, 0, 1;
  Eigen::Vector3d t(1, -2, 3);
  const double s = 2.0;
  Eigen::MatrixXd p(3, 5);
  p << 1, 0, 0, 1, 1,
       0, 1, 0, 1, 0,
       0, 0, 1, 0, 1;
  Eigen::MatrixXd q = (s * R * p).colwise() + t;
  auto req = basicRequest(p, q, Eigen::VectorXd::Ones(5),
                          TransformMode::SimilarityProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  CHECK_CLOSE(result.determinant, 1.0, 1e-12);
  CHECK_CLOSE(result.scale, 2.0, 1e-10);
  CHECK_CLOSE(result.rmse, 0.0, 1e-10);
  CHECK((result.rotation - R).cwiseAbs().maxCoeff() < 1e-9);
  CHECK((result.translation - t).cwiseAbs().maxCoeff() < 1e-9);
  CHECK(result.scale_identifiable);
}

// 3. Orthogonal mode must not absorb scale. With centred symmetric points the
// optimal rigid R is I and the residual has a hand-computed value.
TEST_CASE("fit/orthogonal2d-refuses-scale-with-known-residual") {
  // Centred unit square (weighted mean zero, isotropic: optimum R = I).
  Eigen::MatrixXd p(2, 4);
  p << -1, 1, -1, 1,
       -1, -1, 1, 1;
  Eigen::MatrixXd q = 3.0 * p;
  auto req = basicRequest(p, q, Eigen::VectorXd::Ones(4),
                          TransformMode::OrthogonalProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  CHECK_CLOSE(result.scale, 1.0, 1e-12);
  // With R=I, t=0: sse = sum_i ||3 p_i - p_i||^2 = 4 * 8 = 32.
  CHECK_CLOSE(result.sse, 32.0, 1e-9);
  CHECK_CLOSE(result.translation.norm(), 0.0, 1e-9);
  // Similarity mode recovers scale 3 with zero residual.
  auto sim_req = req;
  sim_req.mode = TransformMode::SimilarityProper;
  auto sim = procrustes::fit(sim_req);
  CHECK_CLOSE(sim.scale, 3.0, 1e-10);
  CHECK_CLOSE(sim.rmse, 0.0, 1e-10);
}

// 4. Reflection fixture: only reflection-allowed gives zero residual and a
// determinant of -1; proper mode returns the independently known SSE=4.
TEST_CASE("fit/reflection2d-mode-separation") {
  Eigen::MatrixXd p(2, 4);
  p << 0, 1, 0, 1,
       0, 0, 1, 1;
  Eigen::Matrix2d F;
  F << 1, 0, 0, -1;
  Eigen::Vector2d t(-1, 4);
  Eigen::MatrixXd q = (F * p).colwise() + t;

  auto allow = basicRequest(p, q, Eigen::VectorXd::Ones(4),
                            TransformMode::OrthogonalAllowReflection);
  auto ra = procrustes::fit(allow);
  CHECK(ra.ok);
  CHECK_CLOSE(ra.determinant, -1.0, 1e-12);
  CHECK_CLOSE(ra.rmse, 0.0, 1e-10);
  CHECK((ra.rotation - F).cwiseAbs().maxCoeff() < kEps);

  auto proper = basicRequest(p, q, Eigen::VectorXd::Ones(4),
                             TransformMode::OrthogonalProper);
  auto rp = procrustes::fit(proper);
  CHECK(rp.ok);
  CHECK_CLOSE(rp.determinant, 1.0, 1e-12);
  // Hand-derived for the unit square: best proper turn is 180 degrees and
  // every residual has norm 1, so sse = 4.
  CHECK_CLOSE(rp.sse, 4.0, 1e-9);
}

// 5. Collinear 2D points: proper rotation is uniquely determined (90 degrees),
// while allowing reflection yields TWO zero-residual orthogonal answers.
TEST_CASE("fit/collinear2d-proper-unique-reflect-nonunique") {
  Eigen::MatrixXd p(2, 5);
  Eigen::MatrixXd q(2, 5);
  for (int i = 0; i < 5; ++i) {
    const double x = static_cast<double>(i) - 2.0;
    p.col(i) << x, 0.0;
    q.col(i) << 0.0, x;
  }
  auto w = Eigen::VectorXd::Ones(5);

  auto proper = basicRequest(p, q, w, TransformMode::OrthogonalProper);
  auto rp = procrustes::fit(proper);
  CHECK(rp.ok);
  CHECK(rp.rotation_unique);
  CHECK_CLOSE(rp.rmse, 0.0, 1e-10);
  Eigen::Matrix2d expected_R;
  expected_R << 0, -1, 1, 0;
  CHECK((rp.rotation - expected_R).cwiseAbs().maxCoeff() < kEps);

  auto allow = basicRequest(p, q, w,
                            TransformMode::OrthogonalAllowReflection);
  auto ra = procrustes::fit(allow);
  CHECK(ra.ok);
  CHECK(!ra.rotation_unique);
  CHECK_CLOSE(ra.rmse, 0.0, 1e-10);
  bool has_nonunique = false;
  for (const auto& dg : ra.diagnostics) {
    if (dg.code == FailureCode::SingularNonUnique) has_nonunique = true;
  }
  CHECK(has_nonunique);

  // Exhibit the alternative, genuinely different zero-residual answer.
  Eigen::Matrix2d alt_R;
  alt_R << 0, 1, 1, 0;  // reflection mapping the x-axis to the y-axis
  CHECK_CLOSE(alt_R.determinant(), -1.0, 1e-15);
  double alt_sse = 0.0;
  for (int i = 0; i < p.cols(); ++i) {
    alt_sse += (q.col(i) - alt_R * p.col(i)).squaredNorm();
  }
  CHECK_CLOSE(alt_sse, ra.sse, 1e-9);  // both optimal: 0
}

// 6. Collinear 3D points: proper rotation is non-unique; many proper R map
// the x-axis to the z-axis, all with zero residual.
TEST_CASE("fit/collinear3d-proper-nonunique-multiplicity") {
  Eigen::MatrixXd p(3, 5);
  Eigen::MatrixXd q(3, 5);
  for (int i = 0; i < 5; ++i) {
    const double x = static_cast<double>(i) - 2.0;
    p.col(i) << x, 0.0, 0.0;
    q.col(i) << 0.0, 0.0, x;
  }
  auto w = Eigen::VectorXd::Ones(5);
  auto req = basicRequest(p, q, w, TransformMode::OrthogonalProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  CHECK(!result.rotation_unique);
  CHECK_CLOSE(result.rmse, 0.0, 1e-10);

  // Two distinct proper rotations mapping e1 -> e3.
  Eigen::Matrix3d r1;
  r1 << 0, 0, 1,
        0, -1, 0,
        1, 0, 0;
  Eigen::Matrix3d r2;
  r2 << 0, 0, 1,
        0, 1, 0,
        1, 0, 0;
  // r2 det = -1; correct it to a proper representative different from r1.
  r2 << 0, 1, 0,
        0, 0, 1,
        1, 0, 0;
  CHECK_CLOSE(r1.determinant(), 1.0, 1e-12);
  CHECK_CLOSE(r2.determinant(), 1.0, 1e-12);
  CHECK((r1 - r2).cwiseAbs().maxCoeff() > 0.5);
  auto sse_for = [&](const Eigen::Matrix3d& R) {
    double sse = 0.0;
    for (int i = 0; i < p.cols(); ++i) {
      sse += (q.col(i) - R * p.col(i)).squaredNorm();
    }
    return sse;
  };
  CHECK_CLOSE(sse_for(r1), 0.0, 1e-9);
  CHECK_CLOSE(sse_for(r2), 0.0, 1e-9);
}

// 7. Coincident source under similarity: scale not identifiable.
TEST_CASE("fit/coincident-source-scale-unidentifiable") {
  Eigen::MatrixXd p(2, 3);
  p << 4, 4, 4,
       7, 7, 7;
  Eigen::MatrixXd q(2, 3);
  q << 1, 2, 3,
       9, 8, 7;
  auto req = basicRequest(p, q, Eigen::VectorXd::Ones(3),
                          TransformMode::SimilarityProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  CHECK(!result.scale_identifiable);
  CHECK(!result.rotation_unique);
  bool scale_diag = false;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity == procrustes::Severity::Warning &&
        dg.step == "scale-identifiability") {
      scale_diag = true;
    }
  }
  CHECK(scale_diag);
  // Any scale produces the same prediction: s R pbar cancels via translation.
  // Check that two different scales yield the same minimum SSE.
  const Eigen::Vector2d tbar = q.rowwise().mean();
  double expected_sse = 0.0;
  for (int i = 0; i < q.cols(); ++i) {
    expected_sse += (q.col(i) - tbar).squaredNorm();
  }
  CHECK_CLOSE(result.sse, expected_sse, 1e-9);
}

// 8. Weights change the answer. A single corrupted pair is ignored at zero
// weight (identity optimum) but steers the fit when heavily weighted; both
// reference angles come from an independent closed-form 2D calculation.
TEST_CASE("fit/weights-change-result-closed-form-angle") {
  // Centred square mapped by the identity.
  Eigen::MatrixXd p(2, 4);
  p << -1, 1, -1, 1,
       -1, -1, 1, 1;
  Eigen::MatrixXd q = p;
  // Corrupt pair 3: rotate only that target by 90 degrees (about origin).
  q.col(3) << -p(1, 3), p(0, 3);  // (1,1) -> (-1,1)

  auto angleOf = [&](const Eigen::VectorXd& w) {
    const double W = w.sum();
    Eigen::Vector2d pb =
        ((p.array().rowwise() * w.transpose().array()).matrix().rowwise().sum()) / W;
    Eigen::Vector2d qb =
        ((q.array().rowwise() * w.transpose().array()).matrix().rowwise().sum()) / W;
    double h00 = 0, h01 = 0, h10 = 0, h11 = 0;
    for (int i = 0; i < p.cols(); ++i) {
      const Eigen::Vector2d a = p.col(i) - pb;
      const Eigen::Vector2d b = q.col(i) - qb;
      h00 += w(i) * a(0) * b(0);
      h01 += w(i) * a(0) * b(1);
      h10 += w(i) * a(1) * b(0);
      h11 += w(i) * a(1) * b(1);
    }
    // Optimal 2D proper rotation angle: tan(a) = (H01 - H10)/(H00 + H11).
    return std::atan2(h01 - h10, h00 + h11);
  };

  Eigen::VectorXd w_down(4);
  w_down << 1, 1, 1, 0.0;
  Eigen::VectorXd w_up(4);
  w_up << 1, 1, 1, 100.0;
  const double ref_angle_down = angleOf(w_down);
  const double ref_angle_up = angleOf(w_up);

  auto rd = procrustes::fit(basicRequest(p, q, w_down,
                                         TransformMode::OrthogonalProper));
  auto ru = procrustes::fit(basicRequest(p, q, w_up,
                                         TransformMode::OrthogonalProper));
  auto core_angle = [](const Eigen::MatrixXd& R) {
    return std::atan2(R(1, 0), R(0, 0));
  };
  CHECK_CLOSE(core_angle(rd.rotation), ref_angle_down, 1e-9);
  CHECK_CLOSE(core_angle(ru.rotation), ref_angle_up, 1e-9);
  CHECK(std::abs(ref_angle_down) < 1e-9);       // identity when ignored
  // Heavy weighting pulls the rotation substantially (towards
  // the corrupted pair's 90-degree turn); it is not exactly 90 degrees
  // because the corrupt point also shifts the weighted centroid.
  CHECK(ref_angle_up > 0.7);
  CHECK(rd.rmse < 1e-9);
  CHECK(ru.rmse > 0.1);
}

// 9. Noisy weighted fixture: kernel SSE matches an independent angle scan.
TEST_CASE("fit/weighted-noisy2d-matches-independent-angle-scan") {
  Eigen::MatrixXd p(2, 4);
  p << -1, 1, -1, 1,
       -1, -1, 1, 1;
  Eigen::Matrix2d R;
  R << 0, -1, 1, 0;
  Eigen::Vector2d t(3, 5);
  Eigen::MatrixXd noise(2, 4);
  noise << 0.10, -0.20, 0.30, -0.20,
           0.00, 0.10, -0.20, 0.10;
  Eigen::Vector4d w(2, 1, 1, 2);
  Eigen::Vector2d nw = noise * w;
  for (int i = 0; i < 4; ++i) noise.col(i) -= nw / w.sum();
  Eigen::MatrixXd q = (R * p).colwise() + t + noise;

  auto req = basicRequest(p, q, w, TransformMode::OrthogonalProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  const auto ref = bruteForceRigid2D(p, q, w);
  CHECK_CLOSE(result.sse, ref.sse, 2e-5);
  CHECK_CLOSE(result.rmse, std::sqrt(ref.sse / w.sum()), 2e-5);
}

// 10. Failure categories are reported with specific codes, not generic errors.
TEST_CASE("fit/failure-categories-are-specific") {
  Request req;
  req.request_id = "req-empty";
  auto r0 = procrustes::fit(req);
  CHECK(!r0.ok);
  CHECK(r0.code == FailureCode::EmptyInput);

  Request req2;
  req2.request_id = "req-size";
  req2.source = Eigen::MatrixXd(2, 3);
  req2.target = Eigen::MatrixXd(2, 2);
  req2.weights = Eigen::VectorXd::Ones(3);
  auto r1 = procrustes::fit(req2);
  CHECK(!r1.ok);
  CHECK(r1.code == FailureCode::SizeMismatch);

  Request req3;
  req3.request_id = "req-dim";
  req3.source = Eigen::MatrixXd(2, 2);
  req3.target = Eigen::MatrixXd(3, 2);
  req3.weights = Eigen::VectorXd::Ones(2);
  auto r2 = procrustes::fit(req3);
  CHECK(!r2.ok);
  CHECK(r2.code == FailureCode::DimensionMismatch);

  Request req4;
  req4.request_id = "req-neg";
  req4.source = Eigen::MatrixXd(2, 2);
  req4.target = Eigen::MatrixXd(2, 2);
  req4.weights = Eigen::Vector2d(-1.0, 1.0);
  auto r3 = procrustes::fit(req4);
  CHECK(!r3.ok);
  CHECK(r3.code == FailureCode::NegativeWeight);

  Request req5;
  req5.request_id = "req-zero";
  req5.source = Eigen::MatrixXd(2, 2);
  req5.target = Eigen::MatrixXd(2, 2);
  req5.weights = Eigen::Vector2d(0.0, 0.0);
  auto r4 = procrustes::fit(req5);
  CHECK(!r4.ok);
  CHECK(r4.code == FailureCode::TotalWeightZero);

  Request req6;
  req6.request_id = "req-nan";
  req6.source = Eigen::MatrixXd(2, 2);
  req6.source.setConstant(std::numeric_limits<double>::quiet_NaN());
  req6.target = Eigen::MatrixXd(2, 2);
  req6.weights = Eigen::VectorXd::Ones(2);
  auto r5 = procrustes::fit(req6);
  CHECK(!r5.ok);
  CHECK(r5.code == FailureCode::NaNOrInf);
}

// 11. Per-point residual norms are consistent with the reported SSE/RMSE.
TEST_CASE("fit/residual-norms-consistent") {
  Eigen::MatrixXd p(2, 3);
  p << 0, 2, 1,
       0, 1, 3;
  Eigen::Matrix2d R;
  R << 0, -1, 1, 0;
  Eigen::Vector2d t(4, -1);
  Eigen::MatrixXd q = (R * p).colwise() + t;
  q(0, 1) += 0.3;
  Eigen::Vector3d w(1, 2, 0.5);
  auto req = basicRequest(p, q, w, TransformMode::OrthogonalProper);
  auto result = procrustes::fit(req);
  CHECK(result.ok);
  double sse = 0.0;
  for (int i = 0; i < p.cols(); ++i) {
    const Eigen::Vector2d fitted =
        result.scale * result.rotation * p.col(i) + result.translation;
    CHECK_CLOSE(result.residual_norms(i), (q.col(i) - fitted).norm(), 1e-12);
    sse += w(i) * result.residual_norms(i) * result.residual_norms(i);
  }
  CHECK_CLOSE(result.sse, sse, 1e-10);
  CHECK_CLOSE(result.rmse, std::sqrt(sse / w.sum()), 1e-12);
}

// 12. Mode string parsing round-trips and rejects unknown tokens.
TEST_CASE("fit/mode-parsing") {
  using procrustes::parseMode;
  TransformMode m;
  CHECK(parseMode("rigid", m));
  CHECK(m == TransformMode::OrthogonalProper);
  CHECK(parseMode("rigid-reflect", m));
  CHECK(m == TransformMode::OrthogonalAllowReflection);
  CHECK(parseMode("similarity", m));
  CHECK(m == TransformMode::SimilarityProper);
  CHECK(parseMode("similarity-reflect", m));
  CHECK(m == TransformMode::SimilarityAllowReflection);
  CHECK(!parseMode("warp-drive", m));
}

// 13. 3D planar (rank 2) points: proper rotation stays unique, but allowing
// reflection makes the map non-unique (mirroring about the plane leaves all
// points unchanged).
TEST_CASE("fit/planar3d-reflection-nonunique-proper-unique") {
  // Four points spanning the xy-plane (z = 0), mapped by identity.
  Eigen::MatrixXd p(3, 4);
  p << -1, 1, -1, 1,
       -1, -1, 1, 1,
        0, 0, 0, 0;
  Eigen::MatrixXd q = p;
  auto w = Eigen::VectorXd::Ones(4);

  auto proper = basicRequest(p, q, w, TransformMode::OrthogonalProper);
  auto rp = procrustes::fit(proper);
  CHECK(rp.ok);
  CHECK(rp.rotation_unique);
  CHECK_CLOSE(rp.rmse, 0.0, 1e-9);
  CHECK_CLOSE(rp.determinant, 1.0, 1e-12);

  auto allow = basicRequest(p, q, w, TransformMode::OrthogonalAllowReflection);
  auto ra = procrustes::fit(allow);
  CHECK(ra.ok);
  CHECK(!ra.rotation_unique);
  CHECK_CLOSE(ra.rmse, 0.0, 1e-9);

  // Independent second answer: mirror across the xy-plane, det = -1.
  Eigen::Matrix3d mirror_xy;
  mirror_xy << 1, 0, 0,
               0, 1, 0,
               0, 0, -1;
  CHECK_CLOSE(mirror_xy.determinant(), -1.0, 1e-12);
  double sse = 0.0;
  for (int i = 0; i < p.cols(); ++i) {
    sse += (q.col(i) - mirror_xy * p.col(i)).squaredNorm();
  }
  CHECK_CLOSE(sse, 0.0, 1e-9);
}
