#pragma once
// Public numerical contract for the Pade approximation engine.
// All floating point computation is performed in long double.
#include <Eigen/Dense>
#include <string>
#include <vector>

namespace pade {

using Real = long double;
using Vector = Eigen::Matrix<Real, Eigen::Dynamic, 1>;

// Failure / warning categories. They are part of the public contract:
// callers and tests branch on these values rather than on message text.
enum class StatusCode {
    Ok,                                 // normalized [m/n] approximant produced
    RankDeficient,                      // A is singular but consistent; min-norm representative
    DegenerateDenominatorConstant,      // q0 == 1 normalization is impossible (block Pade case)
    ResidualMismatch,                   // solved coefficients fail to reproduce the input series
    DenominatorNearZero,                // evaluation point is (numerically) on a pole
    InvalidArgument,                    // malformed request (bad order, truncated series, ...)
    NumericalFailure                    // SVD / linear algebra did not converge
};

const char* toString(StatusCode code) noexcept;
bool isFailure(StatusCode code) noexcept;   // true when no normalized approximant exists

struct SolveOptions {
    // Singular values below factor * eps * sigma_max are treated as zero.
    Real rank_tol_factor = Real(1e-12);
    // |r_k| <= residual_tol * (1 + |c_k|) means coefficient k is matched.
    Real residual_tol  = Real(1e-14);
    // Relative leading-coefficient threshold for polynomial trim / GCD.
    Real gcd_tol       = Real(1e-13);
    // Number of extra residual coefficients computed beyond m + n + 1.
    int residual_extra_terms = 6;
    // Whether the common factor of P and Q is located and divided out.
    bool remove_common_factor = true;
};

struct SolveReport {
    StatusCode status = StatusCode::InvalidArgument;
    std::string reason;                 // human readable explanation of the category
    std::string run_id;                 // correlates every log line with this run

    int m = 0;
    int n = 0;
    int required_series_len = 0;        // m + n + 1 coefficients needed

    // Linear algebra diagnostics for the Toeplitz block (n x n).
    int matrix_rank = -1;
    Vector singular_values;
    Real condition_number = Real(0);
    bool q0_normalized = false;         // false exactly in the degenerate case

    // UNREDUCED local [m/n] definition. When q0 cannot be normalized this is
    // the homogeneous (q0 = 0) representative and is kept for diagnosis.
    Vector p;                           // numerator coefficients, ascending powers
    Vector q;                           // denominator coefficients, ascending powers

    // Residual series R with C - P = Q * R, coefficient by coefficient.
    Vector residual;
    int matched_terms = 0;              // consecutive matched coefficients starting at k = 0
    bool residual_verified = false;

    // Common factor diagnosis. The unreduced arrays above are never overwritten.
    int gcd_degree = -1;
    Vector gcd_coeffs;
    Vector p_reduced;
    Vector q_reduced;
    bool common_factor_removed = false;
    Real reduced_residual_norm = Real(-1);

    bool ok() const noexcept { return !isFailure(status); }
};

struct EvalReport {
    StatusCode status = StatusCode::InvalidArgument;
    std::string reason;
    Real value = 0;
    Real numerator = 0;
    Real denominator = 0;
    Real x = 0;
    bool used_reduced = false;
};

struct SeriesRequest {
    int m = 0;
    int n = 0;
    std::vector<Real> coefficients;     // c_0, c_1, ...
    SolveOptions options;
};

std::string to_string(const Vector& v);  // debug rendering

} // namespace pade
