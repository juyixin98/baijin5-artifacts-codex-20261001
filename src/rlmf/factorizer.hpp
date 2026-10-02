#pragma once

#include "rlmf/error.hpp"
#include "rlmf/linalg.hpp"

#include <cstdint>
#include <optional>
#include <string>

namespace rlmf {

// Fixed-by-contract randomized factorization parameters.
struct FactorizeConfig {
    int target_rank = 0;    // k: requested approximation rank, k >= 1
    int oversampling = 10;  // p: extra random columns, p >= 0 (fixed default)
    int power_iters = 2;    // q: power-iteration steps, each followed by
                            //    re-orthogonalization; q >= 0
    std::uint64_t seed = 0x243f6a8885a308d3ULL; // fixed default seed
    double rank_tol = 1e-12; // breakdown tolerance inside orthonormalization

    // If true, compute the *exact* residual A - U S V^T and its Frobenius norm.
    // The probe estimate is always returned; this is the only "exact" number.
    bool compute_exact_residual = true;
    // If true, compute the full-SVD optimal tail energy (reference only).
    bool compute_reference_tail = true;
};

// Information explaining what the algorithm actually did. Any error figure
// labelled "estimated" is a randomized probe, never the exact optimum.
struct FactorizeDiagnostics {
    int requested_rank = 0;     // k from config
    int sketch_cols = 0;        // k + p
    int achieved_rank = 0;      // numerical rank actually produced (<= k)
    int ortho_rank_Q = 0;       // numerical rank found during first QR
    double frob_A = 0.0;        // ||A||_F
    double rel_frob_approx = 0.0; // ||USV^T||_F / ||A||_F
    // Randomized Gaussian probe: unbiased estimate of ||A - USV^T||_F.
    double est_residual_frob = 0.0;
    // Exact residual, only meaningful when exact_residual_valid.
    bool exact_residual_valid = false;
    double exact_residual_frob = 0.0;
    // Optimal rank-r Frobenius error sqrt(sum_{i>r} sigma_i^2) from a full
    // SVD. Independent reference; valid when reference_valid.
    bool reference_valid = false;
    double reference_tail_frob = 0.0;
    std::string rationale; // human-readable judgement explanation
};

struct Factorization {
    Matrix U; // m x r, orthonormal columns
    Vector S; // r singular values, descending, non-negative
    Matrix V; // n x r, orthonormal columns
    FactorizeDiagnostics diag;

    Matrix approximate() const { return (U * S.asDiagonal()) * V.transpose(); }
};

// Validates config + matrix shape and returns the contract error (if any).
std::optional<Error> validate_request(const Matrix& a, const FactorizeConfig& cfg);

// Strategy for building the orthonormal range basis Q. The production
// implementation is RandomRangeSketch (fixed seed + reorthogonalized power
// iterations). The seam lets tests inject deterministic numerical failures
// and allocation failures instead of exhausting real memory.
class RangeSketch {
public:
    virtual ~RangeSketch() = default;
    // Q = orth(Y0), Y0 = A * random(seed).
    virtual Result<OrthonormResult> initial(const Matrix& a, int ell) = 0;
    // One reorthogonalized power step: orth(A * orth(A^T Q)).
    virtual Result<OrthonormResult> step(const Matrix& a,
                                         const Matrix& q) = 0;
};

class RandomRangeSketch : public RangeSketch {
public:
    explicit RandomRangeSketch(FactorizeConfig cfg) : cfg_(cfg) {}
    Result<OrthonormResult> initial(const Matrix& a, int ell) override;
    Result<OrthonormResult> step(const Matrix& a, const Matrix& q) override;

private:
    FactorizeConfig cfg_;
};

// Core driver with an injected sketch strategy.
Result<Factorization> factorize_with_sketch(const Matrix& a,
                                            const FactorizeConfig& cfg,
                                            RangeSketch& sketch);

// One-shot randomized SVD with the production random sketch.
Result<Factorization> randomized_factorize(const Matrix& a,
                                           const FactorizeConfig& cfg);

} // namespace rlmf
