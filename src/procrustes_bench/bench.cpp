#include "procrustes/bench.hpp"

#include <algorithm>
#include <cstdint>

#include <limits>

namespace procrustes::bench {
namespace {

Eigen::MatrixXd randomRotation(int d, SplitMix64& rng) {
  // Random orthogonal matrix via QR of a random matrix; corrected to have
  // determinant +1 (proper rotation). Independent of the core solver.
  Eigen::MatrixXd a(d, d);
  for (int i = 0; i < d * d; ++i) a(i / d, i % d) = rng.nextUnit();
  Eigen::HouseholderQR<Eigen::MatrixXd> qr(a);
  Eigen::MatrixXd q = qr.householderQ();
  if (q.determinant() < 0.0) {
    q.col(0) *= -1.0;
  }
  return q;
}

Workload cloudFor(int d, int n, SplitMix64 rng, bool collinear, double scale,
                  bool similarity) {
  Workload wl;
  wl.dimension = d;
  wl.point_count = n;
  wl.source = Eigen::MatrixXd(d, n);
  if (collinear) {
    for (int i = 0; i < n; ++i) {
      const double x = 2.0 * static_cast<double>(i) /
                           static_cast<double>(std::max(1, n - 1)) -
                       1.0;
      wl.source.col(i) = x * Eigen::VectorXd::Unit(d, 0);
    }
  } else {
    for (int k = 0; k < d * n; ++k) {
      wl.source(k / n, k % n) = 2.0 * rng.nextUnit();
    }
  }
  const Eigen::MatrixXd r = randomRotation(d, rng);
  Eigen::VectorXd t(d);
  for (int i = 0; i < d; ++i) t(i) = 1.0 + 0.5 * rng.nextUnit();
  const double s = similarity ? scale : 1.0;
  wl.target = (s * r * wl.source).colwise() + t;
  wl.weights = Eigen::VectorXd::Ones(n);
  return wl;
}

}  // namespace

Workload makeRigidWorkload(int dimension, int point_count,
                           std::uint64_t seed) {
  Workload wl = cloudFor(dimension, point_count, SplitMix64(seed), false, 1.0,
                         false);
  wl.name = "rigid_d" + std::to_string(dimension) + "_n" +
            std::to_string(point_count);
  return wl;
}

Workload makeSimilarityWorkload(int dimension, int point_count, double scale,
                                std::uint64_t seed) {
  Workload wl = cloudFor(dimension, point_count, SplitMix64(seed), false, scale,
                         true);
  wl.name = "similarity_d" + std::to_string(dimension) + "_n" +
            std::to_string(point_count);
  return wl;
}

Workload makeCollinearWorkload(int dimension, int point_count,
                               std::uint64_t seed) {
  Workload wl = cloudFor(dimension, point_count, SplitMix64(seed), true, 1.0,
                         false);
  wl.name = "collinear_d" + std::to_string(dimension) + "_n" +
            std::to_string(point_count);
  return wl;
}

}  // namespace procrustes::bench
