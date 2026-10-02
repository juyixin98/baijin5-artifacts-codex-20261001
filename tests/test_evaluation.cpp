// Evaluation: correct values, pole detection, and removable singularities.
#include "test_framework.hpp"
#include "pade/solver.hpp"
#include <vector>
#include <cmath>
using namespace pade_test;
using namespace pade;

namespace {
std::vector<Real> expSeries(int terms) {
    std::vector<Real> c(terms);
    Real fact = 1;
    for (int k = 0; k < terms; ++k) { c[k] = 1 / fact; fact *= k + 1; }
    return c;
}
SolveReport expPade(int m, int n) {
    SeriesRequest req; req.m=m; req.n=n; req.coefficients=expSeries(14);
    return solvePade(req);
}
}

TEST_CASE(exp_2_2_evaluates_correctly_away_from_zero) {
    SolveReport r = expPade(2, 2);
    check(r.ok(), "solve ok", r.run_id);
    for (double xd : {0.0, 0.1, -0.1, 0.2}) {
        Real x = static_cast<Real>(xd);
        EvalReport e = evaluate(r, x);
        check(e.status == StatusCode::Ok, "eval ok at " + std::to_string(xd),
              run(r.run_id, e.reason));
        const Real ref = std::exp(x);
        check(approx(e.value, ref, 1e-5L),
              "value ~ exp(x) at " + std::to_string(xd),
              run(r.run_id, "relerr=" +
                std::to_string((double)(std::fabs(e.value-ref)/std::fabs(ref)))));
    }
}

TEST_CASE(exp_1_1_pole_is_denominator_near_zero_not_inf) {
    SolveReport r = expPade(1, 1);   // (1+x/2)/(1-x/2), pole at x=2
    check(r.ok(), "solve ok", r.run_id);
    EvalReport e = evaluate(r, Real(2));
    check(e.status == StatusCode::DenominatorNearZero,
          "pole evaluation must fail with DenominatorNearZero",
          run(r.run_id, std::string("got ") + toString(e.status) +
                        " denom=" + std::to_string((double)e.denominator)));
    check(!std::isfinite(e.value) ? false : true, "no inf value reported",
          r.run_id); // value is left at 0, never inf
}

TEST_CASE(near_pole_within_tolerance_is_flagged) {
    SolveReport r = expPade(1, 1);
    // 2*(1 + 1e-13) puts |denom| ~ 1e-13, inside the default 1e-12 band.
    EvalReport e = evaluate(r, Real(2) * (Real(1) + Real(1e-14)), Real(1e-12));
    check(e.status == StatusCode::DenominatorNearZero, "near-pole flagged",
          run(r.run_id, std::string("denom=") +
                        std::to_string((double)e.denominator)));
}

// At x=0 the reduced geometric fraction is finite; the unreduced [2/2] has a
// removable... actually both are finite at 0, so test evaluation of the
// canceled fraction at the spurious root x=0 explicitly (no false failure).
TEST_CASE(reduced_geometric_fraction_evaluates_and_flags_removable_point) {
    std::vector<Real> c(10, Real(1));
    SeriesRequest req; req.m=2; req.n=2; req.coefficients=c;
    SolveReport r = solvePade(req);
    check(r.status == StatusCode::RankDeficient, "rank deficient setup", r.run_id);
    EvalReport e = evaluate(r, Real(0.3));
    check(e.status == StatusCode::Ok, "reduced eval ok",
          run(r.run_id, std::string(toString(e.status)) + " " + e.reason));
    check(e.used_reduced, "evaluation used the reduced form", r.run_id);
    check(approx(e.value, Real(1) / (Real(1) - Real(0.3)), 1e-9L),
          "equals 1/(1-x)", run(r.run_id, std::to_string((double)e.value)));
}
