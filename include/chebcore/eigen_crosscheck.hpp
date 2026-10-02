// SPDX-License-Identifier: MIT
//
// Second, independently-coded DCT-I path built on Eigen vectors/matrices.
// Used only in tests/verification: the production kernel is scalar loops; this
// constructs the cosine matrix explicitly and solves the DCT by matrix-vector
// product, so a shared indexing bug is less likely to go unnoticed.
#ifndef CHEBCORE_EIGEN_CROSSCHECK_HPP
#define CHEBCORE_EIGEN_CROSSCHECK_HPP

#include <Eigen/Dense>

#include <cmath>
#include <cstddef>
#include <stdexcept>
#include "chebcore/numeric_contract.hpp"
#include <vector>

namespace chebcore::eigen_xcheck {

// Coefficients a_0..a_n (ordinary sum) from Lobatto-ordered samples f_0..f_n.
inline Eigen::VectorXd dct1_coefficients(const Eigen::VectorXd& f) {
  const long n = f.size() - 1;
  if (n < 1)
    throw std::invalid_argument("dct1_coefficients: need at least 2 samples");
  Eigen::VectorXd a(n + 1);
  for (long k = 0; k <= n; ++k) {
    double s = 0.0;
    for (long j = 0; j <= n; ++j) {
      const double dj = (j == 0 || j == n) ? 0.5 : 1.0;
      s += dj * f[j] *
           std::cos(chebcore::kPi * static_cast<double>(k) * static_cast<double>(j) /
                    static_cast<double>(n));
    }
    const double ck = (k == 0 || k == n) ? 1.0 : 2.0;
    a[k] = ck / static_cast<double>(n) * s;
  }
  return a;
}

// Full cosine matrix C with C_{j,k} = T_k(t_j); useful to assert the exact
// reconstruction identity C a = f at the nodes.
inline Eigen::MatrixXd lobatto_cosine_matrix(std::size_t n) {
  Eigen::MatrixXd C(static_cast<long>(n) + 1, static_cast<long>(n) + 1);
  for (std::size_t j = 0; j <= n; ++j) {
    const double theta = kPi * static_cast<double>(j) / static_cast<double>(n);
    for (std::size_t k = 0; k <= n; ++k)
      C(static_cast<long>(j), static_cast<long>(k)) =
          std::cos(static_cast<double>(k) * theta);
  }
  return C;
}

}  // namespace chebcore::eigen_xcheck

#endif
