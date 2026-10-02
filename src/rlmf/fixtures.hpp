#pragma once

#include "rlmf/linalg.hpp"

#include <vector>

namespace rlmf {

// Synthetic, fully local test problems. Reference data (singular values,
// U/V bases) are generated analytically here; they are NEVER derived from the
// randomized core under test.
struct LowRankProblem {
    Matrix A;       // m x n = U_true diag(s) V_true^T (+ noise if requested)
    Matrix U_true;  // m x r, orthonormal
    Matrix V_true;  // n x r, orthonormal
    Vector sigmas;  // r singular values, descending
};

// Fixed deterministic rotation: Householder reflector of a fixed vector, then
// extended into a full basis, so U_true/V_true do not equal axis directions.
Matrix fixed_orthonormal_basis(Index dim, Index rank, std::uint64_t tag);

// Exact rank-r matrix with prescribed singular values.
LowRankProblem make_low_rank(Index m, Index n, const Vector& sigmas,
                             std::uint64_t tag);

// Small-gap spectrum: sigmas has gap_ratio between sigma_gap and sigma_gap+1.
Vector small_gap_sigmas(int count, int gap_at, double gap_ratio,
                        double decay = 0.85);

// Exact zero matrix fixture.
Matrix make_zero(Index m, Index n);

// Rank-deficient: matrix with rank < min(m,n) built from truncated bases.
LowRankProblem make_rank_deficient(Index m, Index n, int true_rank,
                                   std::uint64_t tag);

// Full SVD reference computed with Eigen (JacobiSVD), independent of the
// randomized kernel. Used only for reference/verification.
struct SvdReference {
    Vector sigmas;
    Matrix U;
    Matrix V;
};
SvdReference full_svd_reference(const Matrix& a);

} // namespace rlmf
