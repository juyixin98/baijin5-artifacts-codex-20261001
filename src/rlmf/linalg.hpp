#pragma once

#include <Eigen/Dense>

#include <cstdint>

namespace rlmf {

using Matrix = Eigen::MatrixXd;
using Vector = Eigen::VectorXd;
using Index = Eigen::Index;

// Deterministic Gaussian N(0,1) sample stream (std::mt19937_64 + Box-Muller).
// The engine and transform are fixed by contract: replay must be bit-reproducible.
class GaussianStream {
public:
    explicit GaussianStream(std::uint64_t seed) noexcept;
    double next() noexcept;
    Matrix matrix(Index rows, Index cols);

private:
    std::uint64_t state_;
    bool cached_ = false;
    double spare_ = 0.0;
    std::uint64_t draws_ = 0;
};

// Thin QR producing columns with orthonormal columns via (full) Householder QR,
// followed by one explicit modified Gram-Schmidt re-orthogonalization pass.
// Returns the numerical column rank: if a column collapses below
// `rank_tol * col0_norm` during MGS, processing stops and only rank columns
// are returned (zero matrices therefore yield rank 0, not garbage columns).
struct OrthonormResult {
    Matrix q;
    Index rank = 0;
};
OrthonormResult orthonormalize(const Matrix& a, double rank_tol = 1e-12) noexcept;

// Check ||Q^T Q - I||_inf, the measure used for orthogonality assertions.
double orthogonality_error(const Matrix& q) noexcept;

// Frobenius norm.
double frob_norm(const Matrix& a) noexcept;

} // namespace rlmf
