// Independent benchmark for the weighted Procrustes kernel. Data is
// synthesized here with hand-written random reference transforms (not by the
// kernel), the fit is timed over repeated runs, and residuals are sanity
// checked. This is a standalone executable, not part of the unit tests.
#include "procrustes/procrustes.hpp"

#include <chrono>
#include <iostream>
#include <random>
#include <vector>

using namespace procrustes;

namespace {

struct CaseSpec {
  int d;
  int n;
  bool similarity;
  bool reflection;
  const char* name;
};

Eigen::MatrixXd random_orthogonal(std::mt19937& rng, int d, bool allow_refl) {
  std::normal_distribution<double> nd(0.0, 1.0);
  Eigen::MatrixXd A(d, d);
  for (int i = 0; i < d * d; ++i) A(i / d, i % d) = nd(rng);
  Eigen::HouseholderQR<Eigen::MatrixXd> qr(A);
  Eigen::MatrixXd Q = qr.householderQ();
  if (!allow_refl && Q.determinant() < 0) Q.col(0) *= -1.0;
  return Q;
}

double run_case(const CaseSpec& spec, int iters) {
  std::mt19937 rng(1234 + spec.n * 7 + spec.d * 13 +
                   (spec.similarity ? 1 : 0) + (spec.reflection ? 2 : 0));
  std::uniform_real_distribution<double> ud(-5.0, 5.0);
  std::uniform_real_distribution<double> wd(0.1, 3.0);

  PointSet ps;
  ps.p = Eigen::MatrixXd(spec.d, spec.n);
  ps.q = Eigen::MatrixXd(spec.d, spec.n);
  ps.w = Eigen::VectorXd(spec.n);
  for (int i = 0; i < spec.n; ++i) {
    for (int k = 0; k < spec.d; ++k) ps.p(k, i) = ud(rng);
    ps.w(i) = wd(rng);
  }
  const Eigen::MatrixXd R =
      random_orthogonal(rng, spec.d, spec.reflection);
  const double s = spec.similarity ? 1.0 + ud(rng) * 0.5 + 1.0 : 1.0;
  Eigen::VectorXd t(spec.d);
  for (int k = 0; k < spec.d; ++k) t(k) = ud(rng);
  ps.q = (s * R * ps.p).colwise() + t;

  FitConfig cfg;
  cfg.estimate_scale = spec.similarity;
  cfg.allow_reflection = spec.reflection;

  FitResult last;
  const auto t0 = std::chrono::steady_clock::now();
  for (int it = 0; it < iters; ++it)
    last = fit(ps, cfg, RequestContext{"bench"});
  const auto t1 = std::chrono::steady_clock::now();
  const double us =
      std::chrono::duration<double, std::micro>(t1 - t0).count() / iters;

  if (last.rms > 1e-7) {
    std::cerr << "benchmark sanity check failed for " << spec.name
              << ": rms=" << last.rms << "\n";
    std::exit(1);
  }
  std::cout << spec.name << ": " << us << " us/fit"
            << "  (rms=" << last.rms << ")\n";
  return us;
}

}  // namespace

int main(int argc, char** argv) {
  int iters = 2000;
  if (argc > 1) iters = std::max(1, std::atoi(argv[1]));
  std::cout << "Weighted Procrustes kernel benchmark (" << iters
            << " iterations/case)\n";
  const std::vector<CaseSpec> cases = {
      {2, 4, false, false, "rigid2d   n=4   "},
      {2, 100, false, false, "rigid2d   n=100 "},
      {2, 100, true, false, "simil2d   n=100 "},
      {3, 100, false, false, "rigid3d   n=100 "},
      {3, 1000, false, false, "rigid3d   n=1000"},
      {3, 1000, true, true, "simil3d   n=1000 reflection"},
  };
  for (const auto& c : cases) run_case(c, iters);
  std::cout << "all benchmark sanity checks passed\n";
  return 0;
}
