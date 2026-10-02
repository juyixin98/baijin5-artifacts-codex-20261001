// Degeneracy diagnostics: rank deficiency, impossible normalization,
// common-factor elimination, near-zero denominator evaluation.
//
// Denominator block convention used by the kernel (rows k=m+1..m+n):
//   B_{i,j} = c_{m+i-j}, i=1..n, j=0..n.
#include <cmath>
#include <vector>

#include "pade/polynomial.hpp"
#include "pade/solver.hpp"
#include "test_framework.hpp"

using pade::Options;
using pade::PadeResult;
using pade::StatusCode;
using pade::Real;

// B for f=1 at [1/2]: rows k=2,3 are [c2,c1,c0]=[0,0,1] and
// [c3,c2,c1]=[0,0,0] => rank 1, nullity 2. Canonical representative
// normalizes to b0=1 and b1=b2=0, giving the exact answer 1/1.
TEST_CASE("rank deficient block: f=1 at [1/2] is nullity 2") {
    std::vector<Real> c = {1.0, 0.0, 0.0, 0.0, 0.0};
    Options o; o.numerator_order = 1; o.denominator_order = 2;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kRankDeficient);
    CHECK(r.rank.numerical_rank == 1);
    CHECK(r.rank.nullity == 2);
    CHECK(r.rank.nullspace.size() == 2);
    CHECK(r.normalized);
    CHECK_CLOSE(r.denominator_full[0], 1.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[1], 0.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[2], 0.0, 1e-12);
    CHECK_CLOSE(r.numerator_full[0], 1.0, 1e-12);
    CHECK(r.residual.matches_to_order);
    // reduction: exact answer 1/1
    CHECK(r.reduction.reduced_m == 0);
    CHECK(r.reduction.reduced_n == 0);
}

// Unique null vector with b0 = 0: f = 1 + x^2 at [1/1].
// B row (k=m+1=2): [c2,c1] = [1,0] -> unique null b* = (0,1).
TEST_CASE("normalization impossible: unique null vector with b0=0") {
    std::vector<Real> c = {1.0, 0.0, 1.0, 0.0, 0.0};
    Options o; o.numerator_order = 1; o.denominator_order = 1;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kNormalizationImpossible);
    CHECK(!r.normalized);
    CHECK(r.rank.numerical_rank == 1);
    CHECK(r.rank.nullity == 1);
    CHECK_CLOSE(std::abs(r.denominator_full[0]), 0.0, 1e-14);
    CHECK_CLOSE(std::abs(r.denominator_full[1]), 1.0, 1e-12);
    // raw local definition retained (not erased):
    CHECK(r.denominator_full.size() == 2);
    CHECK(r.numerator_full.size() == 2);
    CHECK_CLOSE(r.numerator_full[0], 0.0, 1e-12); // c0*b0
    CHECK_CLOSE(r.numerator_full[1], 1.0, 1e-12); // c1*b0+c0*b1
    CHECK(!r.message.empty());
}

// Same phenomenon at [1/2] for f = x^3 (block forces b0=0 uniquely up to scale).
TEST_CASE("normalization impossible: f=x^3 at [1/2]") {
    std::vector<Real> c = {0.0, 0.0, 0.0, 1.0, 0.0};
    Options o; o.numerator_order = 1; o.denominator_order = 2;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kNormalizationImpossible);
    CHECK(!r.normalized);
    CHECK(r.rank.numerical_rank == 1);
    CHECK(r.rank.nullity == 2);
    CHECK_CLOSE(r.denominator_full[0], 0.0, 1e-14);
    CHECK_CLOSE(std::abs(r.denominator_full[1]), 1.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[2], 0.0, 1e-12);
}

// Classic C matrix singular while B has a normalizable null vector:
// f = 1/(1-x) at [1/1] is the clean non-degenerate case (C 1x1 = -c2=-1
// is nonsingular). The singular-C case 1/(1-x) at [2/2] is a genuine
// Froissart block defect (B rank 1, nullity 2), tested separately below.
TEST_CASE("valid normalization at [1/1] for geometric series") {
    std::vector<Real> c(7, 1.0);
    Options o; o.numerator_order = 1; o.denominator_order = 1;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK_MSG(r.status == StatusCode::kOk, pade::statusName(r.status));
    CHECK(r.rank.truncated_rank == 1);
    CHECK(r.rank.nullity == 1);
    CHECK_CLOSE(r.denominator_full[0], 1.0, 1e-13);
    CHECK_CLOSE(r.denominator_full[1], -1.0, 1e-12);
    CHECK_CLOSE(r.numerator_full[0], 1.0, 1e-13);
    CHECK(r.residual.matches_to_order);
}

// Singular classic C together with Froissart block defect:
// f = 1/(1-x) at [2/2]. B rows k=3,4: [1,1,1],[1,1,1] -> rank 1,
// nullity 2. SVD representative is normalized, matched through order 4.
TEST_CASE("singular C with Froissart defect: 1/(1-x) at [2/2]") {
    std::vector<Real> c(7, 1.0);
    Options o; o.numerator_order = 2; o.denominator_order = 2;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kRankDeficient);
    CHECK(r.rank.truncated_rank == 1);
    CHECK(r.rank.numerical_rank == 1);
    CHECK(r.rank.nullity == 2);
    CHECK(r.normalized);
    CHECK_CLOSE(r.denominator_full[0], 1.0, 1e-10);
    CHECK(r.residual.matches_to_order);
    // the reduced form of the SVD representative must be a ratio agreeing
    // with 1/(1-x) at a sample point (both representatives of the family)
    pade::EvalResult e = pade::evaluatePade(r, 0.5);
    CHECK_CLOSE(e.value, 2.0, 1e-8);
}

// Exact polynomial common factor (Froissart): f=1+x at [2/2].
// Hand calc: b1=-1, b2=0, A=1-x^2=(1-x)(1+x); reduced = (1+x)/1.
// This occurs with nullity 2 (denominator block rank deficient), which is
// exactly why the full local definition must be preserved.
TEST_CASE("common factor elimination preserves local definition") {
    std::vector<Real> c = {1.0, 1.0, 0.0, 0.0, 0.0};
    Options o; o.numerator_order = 2; o.denominator_order = 2;
    o.gcd_tol = 1e-7; // Froissart SVD representative: detect near common factor
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kRankDeficient);
    // canonical minimal representative is A=1+x, B=1 (degree 2 slot zero);
    // the full coefficient vectors at the requested [2/2] orders remain.
    CHECK(r.numerator_full.size() == 3);
    CHECK(r.denominator_full.size() == 3);
    CHECK_CLOSE(r.numerator_full[0], 1.0, 1e-12);
    CHECK_CLOSE(r.numerator_full[1], 1.0, 1e-12);
    CHECK_CLOSE(r.numerator_full[2], 0.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[0], 1.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[1], 0.0, 1e-12);
    CHECK_CLOSE(r.denominator_full[2], 0.0, 1e-12);
    // reduced equals the minimal form (1+x)/1; no polynomial gcd invented
    CHECK(r.reduction.polynomial_gcd_degree == 0);
    CHECK(r.reduction.reduced_m == 1);
    CHECK(r.reduction.reduced_n == 0);
    CHECK_CLOSE(r.numerator_reduced[0], 1.0, 1e-10);
    CHECK_CLOSE(r.numerator_reduced[1], 1.0, 1e-10);
    // local definition slot information untouched:
    CHECK(r.numerator_full.size() == 3);
    CHECK(r.requested_m == 2 && r.requested_n == 2);
    CHECK(r.residual.matches_to_order);
}

// Polynomial GCD primitive directly (independent of the solver path).
TEST_CASE("approximate polynomial GCD detects explicit shared factor") {
    // (1+x)(1+2x)=1+3x+2x^2 and (1+x)(1-x)=1-x^2
    std::vector<Real> a = {1.0, 3.0, 2.0};
    std::vector<Real> b = {1.0, 0.0, -1.0};
    auto g = pade::poly::approximateGcd(a, b, 1e-9);
    CHECK(g.degree == 1);
    CHECK_CLOSE(g.gcd[0], 1.0, 1e-9); // monic gcd = x+1
    // coprime pair: 1+x and 1+2x
    auto h = pade::poly::approximateGcd({1.0, 1.0}, {1.0, 2.0}, 1e-9);
    CHECK(h.degree == 0);
}

TEST_CASE("near-zero denominator evaluation is flagged, not silent success") {
    std::vector<Real> c(8, 1.0); // 1/(1-x)
    Options o; o.numerator_order = 1; o.denominator_order = 1;
    PadeResult r = pade::padeApproximate(c, o);
    CHECK(r.status == StatusCode::kOk);
    pade::EvalResult e1 = pade::evaluatePade(r, 1.0);
    CHECK(e1.status == StatusCode::kPoleEvaluated);
    CHECK(e1.near_pole);
    CHECK(e1.denominator == 0.0);
    CHECK(std::isinf(e1.value));
    pade::EvalResult e2 = pade::evaluatePade(r, 0.5);
    CHECK(e2.status == StatusCode::kOk);
    CHECK_CLOSE(e2.value, 2.0, 1e-12);
}

// Near-degenerate (approximate Froissart doublet): coefficients of the
// rational (1+x)/(1 - eps*x) expanded as a power series. At [2/2] the block
// is numerically near singular; the approximate GCD stage must recover the
// cancellation structure without erasing the full local definition.
TEST_CASE("near Froissart doublet reports approximate cancellation") {
    const Real eps = 1e-6;
    std::vector<Real> c(7);
    // 1/(1-eps x)=sum eps^k x^k ; multiply by (1+x):
    // c_0=1; c_k = eps^k + eps^(k-1) for k>=1.
    Real epow = 1.0;
    c[0] = 1.0;
    for (int k = 1; k < 7; ++k) {
        epow *= eps;
        c[k] = epow + epow / eps; // eps^k + eps^(k-1)
    }
    Options o; o.numerator_order = 2; o.denominator_order = 2;
    o.gcd_tol = 1e-7;
    PadeResult r = pade::padeApproximate(c, o);
    // full definition preserved
    CHECK(r.numerator_full.size() == 3);
    CHECK(r.denominator_full.size() == 3);
    CHECK_CLOSE(r.denominator_full[0], 1.0, 1e-9);
    // exact rational value (1+x)/(1-eps x) at x=0.5
    pade::EvalResult e = pade::evaluatePade(r, 0.5);
    Real exact = (1.5) / (1.0 - eps * 0.5);
    CHECK_CLOSE(e.value, exact, 1e-6);
}
