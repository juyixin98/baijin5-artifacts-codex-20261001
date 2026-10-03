// Minimal vertical-slice test: weighted rigid fit to a hand-made 90-degree
// rotation plus translation, asserting concrete numeric results.
#include "procrustes/procrustes.hpp"

#include <cmath>
#include <iostream>

#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::cerr << "CHECK failed: " #cond " (line " << __LINE__ << ")\n";    \
      return 1;                                                              \
    }                                                                        \
  } while (0)

int main() {
  using namespace procrustes;
  Eigen::MatrixXd p(2, 4);
  p << 0, 1, 1, 0,
       0, 0, 1, 1;
  Eigen::MatrixXd R(2, 2);
  R << 0, -1,
       1,  0;
  Eigen::VectorXd t(2);
  t << 2.0, -3.0;
  Eigen::MatrixXd q = (R * p).colwise() + t;
  Eigen::VectorXd w(4);
  w << 1, 2, 2, 1;

  FitConfig cfg;  // rotation-only rigid defaults
  RequestContext ctx{"slice-0001"};
  FitResult r = fit({p, q, w}, cfg, ctx);

  CHECK(r.status == Status::Success);
  CHECK(r.rank == 2);
  CHECK(r.scale == 1.0);
  CHECK(std::abs(r.rotation(0, 0) - 0.0) < 1e-12);
  CHECK(std::abs(r.rotation(0, 1) + 1.0) < 1e-12);
  CHECK(std::abs(r.rotation(1, 0) - 1.0) < 1e-12);
  CHECK(std::abs(r.rotation(1, 1) - 0.0) < 1e-12);
  CHECK(std::abs(r.translation(0) - 2.0) < 1e-12);
  CHECK(std::abs(r.translation(1) + 3.0) < 1e-12);
  CHECK(r.rms < 1e-12);
  std::cout << "slice test OK: rigid 90deg fit, rms=" << r.rms << "\n";
  return 0;
}
