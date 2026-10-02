#include "rlmf/linalg.hpp"

#include <cmath>

namespace rlmf {

namespace {
// SplitMix64 step over a fixed 64-bit state. Used instead of std::normal_distribution
// because the standard distribution's algorithm is implementation defined.
std::uint64_t splitmix64(std::uint64_t& s) noexcept {
    s += 0x9e3779b97f4a7c15ULL;
    std::uint64_t z = s;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}
double unit_uniform(std::uint64_t& s) noexcept {
    // Top 53 bits mapped to {0.5/2^53, ...} i.e. strictly inside (0, 1), so
    // log/sqrt in the Box-Muller transform stay finite.
    return (static_cast<double>(splitmix64(s) >> 11) + 0.5) *
           (1.0 / 9007199254740992.0); // 2^53
}
} // namespace

GaussianStream::GaussianStream(std::uint64_t seed) noexcept : state_(seed) {}

double GaussianStream::next() noexcept {
    if (cached_) {
        cached_ = false;
        return spare_;
    }
    double u1 = unit_uniform(state_);
    double u2 = unit_uniform(state_);
    draws_ += 2;
    const double radius = std::sqrt(-2.0 * std::log(u1));
    const double angle = 6.28318530717958647692 * u2;
    spare_ = radius * std::sin(angle);
    cached_ = true;
    return radius * std::cos(angle);
}

Matrix GaussianStream::matrix(Index rows, Index cols) {
    Matrix out(rows, cols);
    for (Index j = 0; j < cols; ++j)
        for (Index i = 0; i < rows; ++i)
            out(i, j) = next();
    return out;
}

OrthonormResult orthonormalize(const Matrix& a, double rank_tol) noexcept {
    OrthonormResult res;
    const Index rows = a.rows();
    const Index cols = a.cols();
    if (rows == 0 || cols == 0) {
        res.q = Matrix(rows, 0);
        return res;
    }

    // Rank-revealing QR with column pivoting is used for the FIRST basis: a
    // plain Householder QR returns unit-norm columns even for a zero matrix,
    // so it cannot reveal numerical rank. The pivoted decomposition reports
    // rank 0 on zeros and the true numerical rank on deficient matrices.
    Eigen::ColPivHouseholderQR<Matrix> qr(a);
    if (rank_tol > 0.0)
        qr.setThreshold(rank_tol); // |R_ii| <= tol relative to max |R_ii|
    Index rank = std::min<Index>(qr.rank(), cols);
    Matrix q = Matrix(qr.matrixQ()).leftCols(rank);

    // Re-orthogonalization (twice-is-enough principle): two MGS sweeps with
    // breakdown detection relative to the SCALE OF THE INPUT, so columns that
    // carry no input energy are dropped even if the QR returned them.
    Matrix kept(rows, rank);
    Index kept_rank = 0;
    const double input_scale = a.norm();
    const double floor = rank_tol * std::max(input_scale, 1.0);
    for (Index j = 0; j < rank; ++j) {
        Vector v = q.col(j);
        for (int sweep = 0; sweep < 2; ++sweep) {
            for (Index k = 0; k < kept_rank; ++k)
                v.noalias() -= kept.col(k) * kept.col(k).dot(v);
        }
        if (v.norm() <= floor)
            break;
        kept.col(kept_rank) = v.normalized();
        ++kept_rank;
    }
    res.rank = kept_rank;
    res.q = kept.leftCols(kept_rank);
    return res;
}

double orthogonality_error(const Matrix& q) noexcept {
    if (q.cols() == 0) return 0.0;
    Matrix g = q.transpose() * q;
    g.diagonal().array() -= 1.0;
    return g.array().abs().maxCoeff();
}

double frob_norm(const Matrix& a) noexcept { return a.norm(); }

} // namespace rlmf
