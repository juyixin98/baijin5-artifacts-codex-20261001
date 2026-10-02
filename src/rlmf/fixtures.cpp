#include "rlmf/fixtures.hpp"

#include <Eigen/SVD>

#include <cmath>

namespace rlmf {

namespace {
Matrix deterministic_rotation(Index dim, std::uint64_t tag) {
    // A dense orthogonal matrix as ONE QR of a deterministic dense Gaussian
    // matrix (O(dim^3)). A product of `dim` dense Householder reflectors would
    // be O(dim^4) and makes fixture generation the bottleneck at large sizes.
    // The RNG stream here is independent of the factorizer sketch stream.
    GaussianStream rng(tag);
    Matrix g(dim, dim);
    for (Index j = 0; j < dim; ++j)
        for (Index i = 0; i < dim; ++i)
            g(i, j) = rng.next();
    Eigen::HouseholderQR<Matrix> qr(g);
    return Matrix(qr.householderQ());
}
} // namespace

Matrix fixed_orthonormal_basis(Index dim, Index rank, std::uint64_t tag) {
    Matrix qfull = deterministic_rotation(dim, tag);
    return qfull.leftCols(rank);
}

LowRankProblem make_low_rank(Index m, Index n, const Vector& sigmas,
                             std::uint64_t tag) {
    LowRankProblem p;
    const Index r = sigmas.size();
    p.U_true = fixed_orthonormal_basis(m, r, tag ^ 0x1111111111111111ULL);
    p.V_true = fixed_orthonormal_basis(n, r, tag ^ 0x2222222222222222ULL);
    p.sigmas = sigmas;
    p.A = p.U_true * sigmas.asDiagonal() * p.V_true.transpose();
    return p;
}

Vector small_gap_sigmas(int count, int gap_at, double gap_ratio,
                        double decay) {
    Vector s(count);
    double v = 1.0;
    for (int i = 0; i < count; ++i) {
        s(i) = v;
        v *= decay;
        if (i + 1 == gap_at)
            v = s(i) * gap_ratio; // force the requested small relative gap
    }
    // ensure strict descending
    for (int i = 1; i < count; ++i)
        if (!(s(i) < s(i - 1))) s(i) = s(i - 1) * (1.0 - 1e-12);
    return s;
}

Matrix make_zero(Index m, Index n) { return Matrix::Zero(m, n); }

LowRankProblem make_rank_deficient(Index m, Index n, int true_rank,
                                   std::uint64_t tag) {
    Vector sigmas(true_rank);
    for (int i = 0; i < true_rank; ++i)
        sigmas(i) = std::pow(0.7, i) * 10.0;
    return make_low_rank(m, n, sigmas, tag);
}

SvdReference full_svd_reference(const Matrix& a) {
    Eigen::JacobiSVD<Matrix> svd(a, Eigen::ComputeFullU | Eigen::ComputeFullV);
    SvdReference ref;
    ref.sigmas = svd.singularValues();
    ref.U = svd.matrixU();
    ref.V = svd.matrixV();
    return ref;
}

} // namespace rlmf
