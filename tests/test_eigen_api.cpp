// Acceptance: the Eigen facade produces the same arbitrary-length result as
// the std API and reconstructs through Eigen vectors.
#include "mathcore/fft_eigen.hpp"
#include "reference/reference_dft.hpp"
#include "test_framework.hpp"
#include <Eigen/Dense>
#include <cmath>

namespace mc = fft::mathcore;

int main() {
  tf::begin("Eigen facade: prime N=29 matches independent reference");
  {
    Eigen::VectorXcd x(29);
    for (int i = 0; i < x.size(); ++i)
      x[i] = {std::cos(0.31 * i + 0.4), std::sin(0.19 * i - 0.2)};
    auto e = mc::fftEigen(x);
    tf::check(e.error.status == fft::common::FftStatus::Ok, "eigen fft");
    tf::check(std::string(e.kernelPath) == "bluestein", "prime -> bluestein");
    std::vector<std::complex<double>> xv(29);
    for (int i = 0; i < 29; ++i) xv[i] = x[i];
    auto refr = fft::reference::directDftDouble(xv, -1);
    double err = 0;
    for (int k = 0; k < 29; ++k)
      err = std::max(err, std::abs(e.out[k] -
          std::complex<double>(refr.out[k].real(), refr.out[k].imag())));
    tf::check(err <= 1e-12, "eigen facade error " + std::to_string(err));
  }
  tf::begin("Eigen facade round trip with 1/N normalization");
  {
    Eigen::VectorXcd x(64);
    for (int i = 0; i < 64; ++i) x[i] = {0.5 * i - 10.0, std::sin(0.2 * i)};
    auto X = mc::fftEigen(x);
    auto back = mc::ifftEigen(X.out);
    double err = (back.out - x).cwiseAbs().maxCoeff();
    tf::check(err <= 1e-11, "eigen round trip " + std::to_string(err));
  }
  return tf::finish();
}
