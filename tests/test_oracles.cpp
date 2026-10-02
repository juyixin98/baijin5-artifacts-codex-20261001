// Independent oracles. Everything asserted here is produced by code that does
// not call the engine under test:
//   * denominator coefficients for n <= 2 via explicit Cramer's rule,
//   * function values via libm's long double exp,
//   * residual identity D_k = (C Q)_k - p_k recomputed independently,
//   * series summation for exp as a cross-check of the input series itself.
#include "test_framework.hpp"
#include "pade/solver.hpp"
#include <cmath>
#include <vector>
using namespace pade_test;
using namespace pade;

namespace oracle {

std::vector<Real> expSeries(int terms) {
    std::vector<Real> c(terms);
    Real fact = 1;
    for (int k = 0; k < terms; ++k) { c[k] = 1 / fact; fact *= k + 1; }
    return c;
}

// Explicit n=1 denominator solve: c_m q1 = -c_{m+1}.
Real cramer1(const std::vector<Real>& c, int m) {
    return -c[m + 1] / c[m];
}

// Explicit n=2 solve for q1,q2:
// | c_m     c_{m-1} | |q1|   | -c_{m+1} |
// | c_{m+1} c_m     | |q2| = | -c_{m+2} |
void cramer2(const std::vector<Real>& c, int m, Real& q1, Real& q2) {
    Real a = c[m],     b = (m - 1 >= 0 ? c[m - 1] : 0);
    Real d = c[m + 1], e = c[m];
    Real r1 = -c[m + 1], r2 = -c[m + 2];
    Real det = a * e - b * d;
    q1 = (r1 * e - b * r2) / det;
    q2 = (a * r2 - r1 * d) / det;
}

// Independent convolution residual: D_k = sum_j q_j c_{k-j} - p_k.
std::vector<Real> directResidual(const std::vector<Real>& c,
                                 const std::vector<Real>& p,
                                 const std::vector<Real>& q, int len) {
    std::vector<Real> d(len, 0);
    for (int k = 0; k < len; ++k) {
        for (int j = 0; j < (int)q.size() && j <= k; ++j)
            if (k - j < (int)c.size()) d[k] += q[j] * c[k - j];
        if (k < (int)p.size()) d[k] -= p[k];
    }
    return d;
}

// Independent Taylor sum of exp (different code path than series generation).
Real taylorExp(Real x, int terms) {
    Real s = 1, term = 1;
    for (int k = 1; k < terms; ++k) { term *= x / k; s += term; }
    return s;
}

} // namespace oracle

TEST_CASE(oracle_cramer1_matches_engine_for_several_m) {
    auto c = oracle::expSeries(12);
    for (int m = 1; m <= 4; ++m) {
        SeriesRequest req; req.m=m; req.n=1; req.coefficients=c;
        SolveReport r = solvePade(req);
        check(r.status == StatusCode::Ok, "ok m=" + std::to_string(m), r.run_id);
        Real want = oracle::cramer1(c, m);
        check(approx(r.q[1], want, 1e-13L),
              "q1 vs Cramer for m=" + std::to_string(m),
              run(r.run_id, "engine=" + std::to_string((double)r.q[1]) +
                            " cramer=" + std::to_string((double)want)));
    }
}

TEST_CASE(oracle_cramer2_matches_engine) {
    auto c = oracle::expSeries(12);
    Real q1, q2;
    oracle::cramer2(c, 2, q1, q2);   // exp [2/2]
    SeriesRequest req; req.m=2; req.n=2; req.coefficients=c;
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::Ok, "ok", r.run_id);
    check(approx(r.q[1], q1, 1e-13L), "q1 vs Cramer2",
          run(r.run_id, std::to_string((double)r.q[1]) + " vs " +
                        std::to_string((double)q1)));
    check(approx(r.q[2], q2, 1e-13L), "q2 vs Cramer2",
          run(r.run_id, std::to_string((double)r.q[2]) + " vs " +
                        std::to_string((double)q2)));
}

TEST_CASE(oracle_independent_residual_confirms_match_order) {
    auto c = oracle::expSeries(12);
    SeriesRequest req; req.m=2; req.n=2; req.coefficients=c;
    SolveReport r = solvePade(req);
    std::vector<Real> pv(r.p.data(), r.p.data() + r.p.size());
    std::vector<Real> qv(r.q.data(), r.q.data() + r.q.size());
    auto d = oracle::directResidual(c, pv, qv, 9);
    for (int k = 0; k < 5; ++k)
        check(std::fabs(d[k]) < 1e-12L,
              "independent residual zero for k=" + std::to_string(k),
              run(r.run_id, "D_" + std::to_string(k) + "=" +
                            std::to_string((double)d[k])));
    // First nonzero term is k = m+n+1 = 5 for exp [2/2].
    check(std::fabs(d[5]) > 1e-8L, "D_5 is nonzero (exact order m+n)",
          run(r.run_id, "D_5=" + std::to_string((double)d[5])));
}

TEST_CASE(oracle_pade_agrees_with_libm_exp_within_derived_bound) {
    auto c = oracle::expSeries(14);
    SeriesRequest req; req.m=3; req.n=3; req.coefficients=c;
    SolveReport r = solvePade(req);
    check(r.ok(), "solve ok", r.run_id);
    for (double xd : {0.2, -0.3, 0.4}) {
        Real x = static_cast<Real>(xd);
        EvalReport e = evaluate(r, x);
        check(e.status == StatusCode::Ok, "eval ok", e.reason);
        Real libm = std::exp(x);
        Real taylor = oracle::taylorExp(x, 14);
        // Engine must agree with BOTH independent references.
        check(approx(e.value, libm, 1e-5L), "vs libm exp",
              run(r.run_id, "relerr=" +
                std::to_string((double)(std::fabs(e.value-libm)/std::fabs(libm)))));
        check(approx(e.value, taylor, 1e-5L), "vs independent Taylor sum",
              run(r.run_id, std::to_string((double)e.value) + " vs " +
                            std::to_string((double)taylor)));
    }
}

// A truncated series (fewer than m+n+1 usable residual terms) still solves but
// the report must not claim verification beyond what the data supports.
TEST_CASE(oracle_minimal_series_still_contractual) {
    auto c = oracle::expSeries(4);   // exactly m+n+1 for [1/2]
    SeriesRequest req; req.m=1; req.n=2; req.coefficients=c;
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::Ok || r.status == StatusCode::RankDeficient,
          "minimal series solvable", run(r.run_id, toString(r.status)));
    check(r.matched_terms >= r.required_series_len,
          "contractual terms verified with exactly m+n+1 coefficients",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
}
