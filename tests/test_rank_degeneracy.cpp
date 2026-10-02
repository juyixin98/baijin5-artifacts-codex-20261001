// Rank deficiency and the infeasible q0 = 1 normalization (block Pade).
// Expected structures are derived by hand from the linear systems, not from
// the engine.
#include "test_framework.hpp"
#include "pade/solver.hpp"
#include <vector>
using namespace pade_test;
using namespace pade;

namespace {
SolveReport solve(const std::vector<Real>& c, int m, int n,
                  SolveOptions opt = {}) {
    SeriesRequest req;
    req.m = m; req.n = n; req.coefficients = c; req.options = opt;
    return solvePade(req);
}
}

// f(x) = 1 + x^2, requested [1/1].
// Equation at order 2: c_2 + c_1 q1 = 1 + 0*q1 = 0  ->  1 = 0, inconsistent.
// q0 = 1 cannot be satisfied: DegenerateDenominatorConstant is mandatory.
TEST_CASE(polynomial_1_plus_x2_at_1_1_is_degenerate) {
    std::vector<Real> c = {1, 0, 1, 0, 0, 0, 0, 0};
    SolveReport r = solve(c, 1, 1);

    check(r.status == StatusCode::DegenerateDenominatorConstant,
          "must flag DegenerateDenominatorConstant",
          run(r.run_id, std::string("got ") + toString(r.status)));
    check(r.ok() == false, "ok() must be false on degeneracy", r.run_id);
    check(isFailure(r.status), "degeneracy is a failure category", r.run_id);
    check(!r.q0_normalized, "q0 must not be normalized to 1", r.run_id);
    check(r.matrix_rank < r.n, "Toeplitz rank deficient",
          run(r.run_id, "rank=" + std::to_string(r.matrix_rank) +
                        " n=" + std::to_string(r.n)));
    check(r.q.size() == 2 && approx(r.q[0], 0, 1e-18L),
          "homogeneous representative has q0=0", run(r.run_id, to_string(r.q)));
    check(std::fabs(r.q[1]) > Real(0.9), "representative carries the null direction q1",
          run(r.run_id, "q1=" + std::to_string((double)r.q[1])));
    // The unreduced local definition must still be present for diagnosis.
    check(r.p.size() == 2, "unreduced numerator retained", r.run_id);
}

// Same function one step earlier, [0/1]: c1 + c0 q1 = 0 + q1 = 0 is feasible.
// This must NOT be reported as the degenerate case.
TEST_CASE(polynomial_1_plus_x2_at_0_1_is_not_degenerate) {
    std::vector<Real> c = {1, 0, 1, 0, 0};
    SolveReport r = solve(c, 0, 1);
    check(r.status != StatusCode::DegenerateDenominatorConstant,
          "feasible normalization must not be degenerate",
          run(r.run_id, toString(r.status)));
    check(r.q0_normalized, "q0=1", r.run_id);
    check(approx(r.q[1], 0), "q1=0", run(r.run_id, to_string(r.q)));
    check(approx(r.p[0], 1), "p0=1", run(r.run_id, to_string(r.p)));
}

// f(x) = 1/(1-x) requested at box [2/2]. The geometric series c_k = 1 makes
// the 2x2 Toeplitz block the all-ones matrix (rank 1). The equations
// q1+q2=-1 (twice) are CONSISTENT, so the minimum-norm q0=1 representative is
// q=(1,-1,0), p=(1,0,0): the exact fraction 1/(1-x) carrying a spurious x
// factor that the GCD diagnosis must surface.
TEST_CASE(geometric_2_2_is_rank_deficient_consistent_with_common_factor) {
    std::vector<Real> c(10, Real(1));
    SolveReport r = solve(c, 2, 2);

    check(r.matrix_rank == 1, "all-ones Toeplitz block has rank 1",
          run(r.run_id, "rank=" + std::to_string(r.matrix_rank)));
    check(r.status == StatusCode::RankDeficient, "RankDeficient (singular but consistent)",
          run(r.run_id, std::string("got ") + toString(r.status) + " " + r.reason));
    check(r.q0_normalized, "q0=1 minimum-norm representative", r.run_id);
    // Minimum-norm solution of the rank-1 nullspace: SVD picks the symmetric
    // point q1=q2=-1/2 (both equal -1/2, sum -1). This is NOT the (1,-1,0)
    // representative; the hand-derived value below is the SVD minimum norm.
    check(approx(r.q[0], 1) && approx(r.q[1], -Real(1)/2, 1e-12L)
          && approx(r.q[2], -Real(1)/2, 1e-12L),
          "q=(1,-1/2,-1/2) minimum-norm representative", run(r.run_id, to_string(r.q)));
    check(approx(r.p[0], 1) && approx(r.p[1], +Real(1)/2, 1e-12L)
          && approx(r.p[2], 0, 1e-12L),
          "p=(1,-1/2,0)", run(r.run_id, to_string(r.p)));
    check(r.gcd_degree == 1, "one spurious common factor detected",
          run(r.run_id, "gcd_degree=" + std::to_string(r.gcd_degree)));
    check(r.common_factor_removed, "reduced fraction stored separately", r.run_id);
    check(r.p_reduced.size() == 1 && std::fabs(r.p_reduced[0]) > 1e-12L,
          "reduced numerator is a nonzero constant",
          run(r.run_id, to_string(r.p_reduced)));
    check(r.q_reduced.size() == 2
          && approx(r.q_reduced[1] / r.q_reduced[0], -1, 1e-10L),
          "reduced denominator proportional to [1,-1]",
          run(r.run_id, to_string(r.q_reduced)));
    // Ratio invariant: reduced fraction at x=0.4 must equal 1/(1-0.4).
    {
        Real x = Real(0.4);
        Real num = r.p_reduced[0];
        Real den = r.q_reduced[0] + r.q_reduced[1] * x;
        check(approx(num / den, Real(1) / (Real(1) - x), 1e-9L),
              "reduced geometric fraction at 0.4",
              run(r.run_id, std::to_string((double)(num / den))));
    }
    check(r.matched_terms >= r.required_series_len,
          "exact geometric fraction matches every supplied term",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
    // Unreduced local definition must survive elimination.
    check(r.p.size() == 3 && r.q.size() == 3, "unreduced [2/2] arrays retained",
          run(r.run_id, "p.size=" + std::to_string(r.p.size())));
}

// A genuinely rank-deficient but CONSISTENT case: take exp at box [2/2] but
// scale the input series so the middle singular value is exactly zero by
// construction. Use f = 1 + x^3 (only c0 and c3 nonzero) at box [2/2]:
//   order 3: c3 + c2 q1 + c1 q2 = 1 = 0            (inconsistent)
// so it is degenerate; for the consistent null case use f = 1 (constant),
// box [1/1]: order 2 reads 0 + 0*q1 = 0, consistent, minimum-norm q1 = 0.
TEST_CASE(constant_series_1_1_is_rank_deficient_consistent) {
    std::vector<Real> c(8);
    c[0] = 1;  // c_k = 0 for k >= 1
    SolveReport r = solve(c, 1, 1);
    check(r.status == StatusCode::RankDeficient, "RankDeficient (singular but consistent)",
          run(r.run_id, std::string("got ") + toString(r.status) + " " + r.reason));
    check(r.matrix_rank == 0, "1x1 zero block rank 0",
          run(r.run_id, "rank=" + std::to_string(r.matrix_rank)));
    check(r.q0_normalized, "q0=1 minimum-norm representative", r.run_id);
    check(approx(r.q[1], 0, 1e-16L), "q1=0", run(r.run_id, to_string(r.q)));
    check(approx(r.p[0], 1) && approx(r.p[1], 0, 1e-16L), "p=(1,0)",
          run(r.run_id, to_string(r.p)));
    check(r.matched_terms >= r.required_series_len, "constant fraction matches",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
}

// Argument validation: too short a series is InvalidArgument, never Ok.
TEST_CASE(short_series_is_invalid_argument) {
    SeriesRequest req;
    req.m = 2; req.n = 2;
    req.coefficients = {1, 1, 1};  // need 5
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::InvalidArgument, "InvalidArgument",
          run(r.run_id, toString(r.status)));
}

TEST_CASE(negative_order_is_invalid_argument) {
    SeriesRequest req;
    req.m = -1; req.n = 1;
    req.coefficients = {1, 1};
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::InvalidArgument, "InvalidArgument for m=-1", r.run_id);
}
