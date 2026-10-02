// Algorithm kernel: Pade [m/n] approximant from power series coefficients.
#pragma once

#include <vector>

#include "pade/types.hpp"

namespace pade {

// Compute Pade [m/n] for f(x) = sum c_k x^k.
// Contract:
//   c.size() >= m+n+1, m,n >= 0, finite coefficients.
// Normalization b0 = 1 is attempted on a representative null vector of the
// full Toeplitz block B_{ij}=c_{i-j} (i=1..n, j=0..n, c_k=0 for k<0).
// Failure modes are reported through result.status, never via exceptions:
//   kRankDeficient            numerical nullity > 1 (best-effort result kept)
//   kNormalizationImpossible  all null-space vectors have |b0| <= tol
PadeResult padeApproximate(const std::vector<Real>& coeffs,
                           const Options& opts = {});

// Evaluate the REDUCED approximant; denominator_full is also reported.
EvalResult evaluatePade(const PadeResult& r, Real x);

// Independently recompute residuals of the FULL local definition.
ResidualReport verifyResiduals(const std::vector<Real>& coeffs,
                               const std::vector<Real>& numerator,
                               const std::vector<Real>& denominator,
                               int m, int n);

} // namespace pade
