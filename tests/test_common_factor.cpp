// Common-factor handling: elimination must not erase the unreduced definition.
#include "test_framework.hpp"
#include "pade/polynomial.hpp"
#include "pade/solver.hpp"
#include <vector>
using namespace pade_test;
using namespace pade;

// Direct checks of the Euclidean GCD kernel against hand-factored polynomials.
TEST_CASE(gcd_finds_known_linear_factor) {
    // p = (1 + x)(1 - 2x) = 1 - x - 2x^2 ; q = (1 + x)(1 + 3x) = 1 + 4x + 3x^2
    Vector p(3); p << 1, -1, -2;
    Vector q(3); q << 1, 4, 3;
    int iters = 0;
    Vector g = poly::gcdMonic(p, q, 1e-12L, iters);
    check(g.size() == 2, "gcd degree 1", "size=" + std::to_string(g.size()));
    // Monic factor proportional to 1+x: coefficient ratio g[0]/g[1] == 1.
    check(approx(g[0] / g[1], Real(1), 1e-12L), "gcd proportional to 1+x",
          "ratio=" + std::to_string((double)(g[0] / g[1])));

    auto dp = poly::divide(p, g, 1e-12L);
    auto dq = poly::divide(q, g, 1e-12L);
    check(approx(dp.quotient[0] / dp.quotient[1], Real(-0.5), 1e-11L),
          "p/g ~ 1-2x", run(std::string(), to_string(dp.quotient)));
    check(approx(dq.quotient[0] / dq.quotient[1], Real(1) / 3, 1e-11L),
          "q/g ~ 1+3x", run(std::string(), to_string(dq.quotient)));
    Real rp = 0, rq = 0;
    for (auto v : dp.remainder) rp = std::max(rp, std::fabs(v));
    for (auto v : dq.remainder) rq = std::max(rq, std::fabs(v));
    check(rp < 1e-10L && rq < 1e-10L, "exact division remainder ~ 0",
          "rp=" + std::to_string((double)rp));
}

TEST_CASE(gcd_of_coprime_polynomials_is_constant) {
    Vector p(2); p << 1, 1;        // 1 + x
    Vector q(2); q << 1, -1;       // 1 - x
    int iters = 0;
    Vector g = poly::gcdMonic(p, q, 1e-12L, iters);
    check(g.size() == 1, "coprime -> constant gcd",
          "size=" + std::to_string(g.size()));
    check(approx(g[0], 1), "monic constant is 1", "");
}

TEST_CASE(trim_preserves_interior_zero) {
    Vector a(3); a << 0, Real(0.5), 0;   // 0.5 x with trailing zero
    Vector t = poly::trim(a, 1e-12L);
    check(t.size() == 2, "trim keeps degree-1", "size=" + std::to_string(t.size()));
    check(approx(t[0], 0) && approx(t[1], 0.5), "0.5 x preserved", "");
}

// Geometric series 1/(1-x) at box [3/3]: minimum-norm unreduced solution is
// (1 + x/3 - x^2/3)/(1 - 2x/3 - x^2/3) = (1/(1-x)) * ((1-x)(1 + x/3 - x^2/3)
// / (1 - 2x/3 - x^2/3)); the shared spurious factor reported by the kernel is
// diagnosed by GCD. The key assertion is that the unreduced [3/3] definition
// is retained while the reduced exact fraction is stored separately.
TEST_CASE(unreduced_definition_retained_after_cancellation) {
    std::vector<Real> c(12, Real(1));   // geometric c_k = 1
    SeriesRequest req; req.m = 3; req.n = 3; req.coefficients = c;
    SolveReport r = solvePade(req);

    check(r.status == StatusCode::RankDeficient,
          "box [3/3] for a [0/1] function is rank deficient",
          run(r.run_id, std::string("status=") + toString(r.status) + " " + r.reason));
    check(r.matrix_rank < 3, "Toeplitz rank below 3",
          run(r.run_id, "rank=" + std::to_string(r.matrix_rank)));

    check(r.p.size() == 4 && r.q.size() == 4,
          "unreduced local [3/3] definition retained",
          run(r.run_id, "p.size=" + std::to_string(r.p.size()) +
                        " q.size=" + std::to_string(r.q.size())));
    check(r.common_factor_removed && r.gcd_degree >= 1,
          "common factor diagnosed",
          run(r.run_id, "gcd_degree=" + std::to_string(r.gcd_degree)));

    // The reduced fraction is proportional to 1/(1-x); assert the ratio
    // invariant rather than the arbitrary monic-GCD scaling.
    check(r.q_reduced.size() == 2
          && approx(r.q_reduced[1] / r.q_reduced[0], -1, 1e-10L),
          "reduced denominator proportional to [1,-1]",
          run(r.run_id, to_string(r.q_reduced)));
    check(r.p_reduced.size() == 1, "reduced numerator is constant",
          run(r.run_id, to_string(r.p_reduced)));
    // Functional identity: reduced fraction at 0.3 == 1/(1-0.3).
    {
        Real x = Real(0.3);
        Real num = 0, den = 0, xk = 1;
        for (Real v : r.p_reduced) { num += v * xk; xk *= x; }
        xk = 1;
        for (Real v : r.q_reduced) { den += v * xk; xk *= x; }
        check(approx(num / den, Real(1) / (Real(1) - x), 1e-9L),
              "reduced fraction equals 1/(1-x) at 0.3",
              run(r.run_id, std::to_string((double)(num / den))));
    }

    // Unreduced residual still matches the series order (it is a valid [3/3]).
    check(r.matched_terms >= r.required_series_len,
          "unreduced form matches through order 6",
          run(r.run_id, "matched=" + std::to_string(r.matched_terms)));
}

// When elimination is disabled via options, the report must not claim removal.
TEST_CASE(common_factor_can_be_disabled_without_losing_definition) {
    std::vector<Real> c(12, Real(1));
    SeriesRequest req; req.m = 3; req.n = 3; req.coefficients = c;
    req.options.remove_common_factor = false;
    SolveReport r = solvePade(req);
    check(!r.common_factor_removed, "no removal when disabled", r.run_id);
    check(r.gcd_degree == -1, "gcd not run", r.run_id);
    check(r.p.size() == 4 && r.q.size() == 4, "full definition present", r.run_id);
}
