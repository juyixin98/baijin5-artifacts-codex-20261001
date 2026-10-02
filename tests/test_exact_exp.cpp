// Hand-computed exponential examples with exact rational Pade coefficients.
// The expected coefficients below are rational arithmetic results, NOT outputs
// of the engine under test.
#include "test_framework.hpp"
#include "pade/solver.hpp"
#include <vector>
using namespace pade_test;

using namespace pade;

namespace {
std::vector<Real> expSeries(int terms) {
    // c_k = 1 / k!
    std::vector<Real> c(terms);
    Real fact = 1;
    for (int k = 0; k < terms; ++k) {
        c[k] = Real(1) / fact;
        fact *= static_cast<Real>(k + 1);
    }
    return c;
}
}

// exp x [1/1] = (1 + x/2)/(1 - x/2)
TEST_CASE(exp_1_1_matches_hand_computed_rationals) {
    SeriesRequest req;
    req.m = 1; req.n = 1;
    req.coefficients = expSeries(8);
    SolveReport r = solvePade(req);

    check(r.status == StatusCode::Ok, "status Ok",
          run(r.run_id, "status=" + std::string(toString(r.status)) + " reason=" + r.reason));
    check(r.q0_normalized, "q0 normalized", r.run_id);
    check(approx(r.q[0], 1), "q0 == 1");
    check(approx(r.q[1], -Real(1)/2, 1e-15L), "q1 == -1/2",
          run(r.run_id, "q1=" + to_string(r.q.segment(1,1))));
    check(approx(r.p[0], 1), "p0 == 1");
    check(approx(r.p[1], Real(1)/2, 1e-15L), "p1 == +1/2",
          run(r.run_id, "p1=" + to_string(r.p.segment(1,1))));
    check(r.matrix_rank == 1, "rank 1", r.run_id);
    check(r.matched_terms >= 3, "at least m+n+1 residual terms vanish",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
    check(r.gcd_degree == 0, "no common factor",
          run(r.run_id, "gcd_degree=" + std::to_string(r.gcd_degree)));
}

// exp x [2/2] = (1 + x/2 + x^2/12)/(1 - x/2 + x^2/12)
TEST_CASE(exp_2_2_matches_hand_computed_rationals) {
    SeriesRequest req;
    req.m = 2; req.n = 2;
    req.coefficients = expSeries(10);
    SolveReport r = solvePade(req);

    check(r.status == StatusCode::Ok, "status Ok",
          run(r.run_id, "status=" + std::string(toString(r.status)) + " reason=" + r.reason));
    const Real p_exp[3] = {1, Real(1)/2, Real(1)/12};
    const Real q_exp[3] = {1, -Real(1)/2, Real(1)/12};
    for (int k = 0; k < 3; ++k) {
        check(approx(r.p[k], p_exp[k], 1e-15L), "p exact " + std::to_string(k),
              run(r.run_id, "got " + to_string(r.p)));
        check(approx(r.q[k], q_exp[k], 1e-15L), "q exact " + std::to_string(k),
              run(r.run_id, "got " + to_string(r.q)));
    }
    check(r.matched_terms >= 5, "5 residual coefficients vanish",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
}

// exp x [3/2] = (1 + 3x/5 + 3x^2/20 + x^3/60)/(1 - 2x/5 + x^2/20)
TEST_CASE(exp_3_2_matches_hand_computed_rationals) {
    SeriesRequest req;
    req.m = 3; req.n = 2;
    req.coefficients = expSeries(11);
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::Ok, "status Ok",
          run(r.run_id, toString(r.status) + (" " + r.reason)));
    const Real p_exp[4] = {1, Real(3)/5, Real(3)/20, Real(1)/60};
    const Real q_exp[3] = {1, -Real(2)/5, Real(1)/20};
    for (int k = 0; k < 4; ++k)
        check(approx(r.p[k], p_exp[k], 1e-14L), "p[" + std::to_string(k) + "] exact", r.run_id);
    for (int k = 0; k < 3; ++k)
        check(approx(r.q[k], q_exp[k], 1e-14L), "q[" + std::to_string(k) + "] exact", r.run_id);
    check(r.matched_terms >= 6, "m+n+1 residual terms vanish",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
}

// [0/0] is the constant c0.
TEST_CASE(exp_0_0_is_constant) {
    SeriesRequest req;
    req.m = 0; req.n = 0;
    req.coefficients = expSeries(4);
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::Ok, "Ok", r.run_id + " " + r.reason);
    check(r.p.size() == 1 && approx(r.p[0], 1), "p=[1]", r.run_id);
    check(r.q.size() == 1 && approx(r.q[0], 1), "q=[1]", r.run_id);
    check(r.matched_terms >= 1, "constant matched", r.run_id);
}
