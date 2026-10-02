// SPDX-License-Identifier: MIT
// Numerical contract layer: public types, error semantics, options.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace pade {

using Real = double;

// Explicit, non-exception status contract. Every code path returns one of
// these; callers must branch on it. kOk is the only success state.
enum class StatusCode : std::int8_t {
    kOk = 0,
    kInvalidArgument,   // bad order / empty series / NaN / inconsistent file
    kInsufficientCoeffs,
    kRankDeficient,     // Toeplitz system singular at requested order
    kNormalizationImpossible, // null space exists but every vector has b0 ~ 0
    kPoleEvaluated,     // evaluation denominator at/near zero (finite sentinel)
    kIoError,
    kInternalError,
};

std::string statusName(StatusCode code);

struct Options {
    int numerator_order = 2;    // m
    int denominator_order = 2;  // n
    Real singular_tol = 0.0;    // 0 => sigma <= maxDim * eps * sigma_max
    Real near_pole_tol = Real(1e-12);
    Real residual_tol = Real(1e-10);
    Real gcd_tol = Real(1e-9);  // relative tolerance for approximate polynomial GCD
};

struct RankDiagnostics {
    int requested_n = 0;
    int numerical_rank = 0;
    int truncated_rank = 0; // rank of classic C-matrix (b1..bn block)
    int nullity = 0;
    Real sigma_max = 0.0;
    Real sigma_min = 0.0;
    Real effective_tol = 0.0;
    // Full b-nullspace basis columns (size (n+1) x nullity), when available.
    // Kept so a reviewer can inspect WHY normalization failed.
    std::vector<std::vector<Real>> nullspace;
};

struct ReductionInfo {
    int monomial_shift = 0;         // powers of x provably shared
    int polynomial_gcd_degree = 0;  // additional approximate non-monomial GCD
    std::vector<Real> gcd_coeffs;   // ascending powers, monic (informational)
    int reduced_m = 0;
    int reduced_n = 0;
};

// Per-order residual r_k = c_k - sum_{j=0..k} a_j b_{k-j}.
struct ResidualReport {
    int checked_from = 0;
    int checked_through = 0; // m+n inclusive
    std::vector<Real> residual; // indexed by power, length checked_through+1
    Real max_abs_residual = 0.0;
    Real tolerance = 0.0;
    bool matches_to_order = false;
    Real next_term_residual = 0.0; // r_{m+n+1} when series allows
    bool next_term_available = false;
};

struct PadeResult {
    StatusCode status = StatusCode::kInternalError;
    std::string message;
    int requested_m = 0;
    int requested_n = 0;
    // Original local definition: coefficients at the *requested* [m/n].
    // These are NEVER deleted by cancellation; reduced forms are separate.
    std::vector<Real> numerator_full;   // length m+1, ascending powers
    std::vector<Real> denominator_full; // length n+1, ascending powers, b0=1 when normalized
    bool normalized = false;
    Real near_pole_tol_used = Real(1e-12);
    RankDiagnostics rank;
    ReductionInfo reduction;
    std::vector<Real> numerator_reduced;
    std::vector<Real> denominator_reduced;
    ResidualReport residual;
};

struct EvalResult {
    StatusCode status = StatusCode::kInternalError;
    std::string message;
    Real x = 0.0;
    Real value = 0.0;
    Real denominator = 0.0;
    bool near_pole = false;
};

} // namespace pade
