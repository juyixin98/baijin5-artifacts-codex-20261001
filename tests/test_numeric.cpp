#include "test_framework.hpp"

#include "rlmf/errors_explained.hpp"
#include "rlmf/fixtures.hpp"
#include "rlmf/factorizer.hpp"
#include "rlmf/linalg.hpp"

#include <sstream>

namespace {

void fill_record(TestContext& ctx, const rlmf::Matrix& a,
                 const rlmf::FactorizeConfig& cfg,
                 const rlmf::Factorization& f) {
    ctx.rec.cfg = cfg;
    ctx.rec.rows = a.rows();
    ctx.rec.cols = a.cols();
    ctx.rec.diag = f.diag;
}

std::string sigmas_str(const rlmf::Vector& s) {
    std::ostringstream os;
    for (long i = 0; i < s.size(); ++i)
        os << (i ? "," : "") << s(i);
    return os.str();
}

} // namespace

// 1) Exact low-rank matrix, fixed analytic SVD, independent JacobiSVD reference.
TEST(numeric_exact_low_rank_anchor) {
    using namespace rlmf;
    Vector sigmas(3);
    sigmas << 5.0, 2.0, 0.5;
    LowRankProblem prob = make_low_rank(40, 30, sigmas, 0xA1A1A1A1ULL);

    FactorizeConfig cfg;
    cfg.target_rank = 3;
    cfg.oversampling = 10;
    cfg.power_iters = 2;
    cfg.seed = 0x243f6a8885a308d3ULL;

    auto r = randomized_factorize(prob.A, cfg);
    CHECK(r.ok());
    if (!r.ok()) { ctx.judge("factorize failed: " + r.error().message); return; }
    const Factorization& f = r.value();
    fill_record(ctx, prob.A, cfg, f);

    ctx.state("sigmas_recovered", sigmas_str(f.S));
    ctx.state("expected_sigmas", "5,2,0.5");

    CHECK(f.S.size() == 3);
    CHECK_CLOSE(f.S(0), 5.0, 1e-9);
    CHECK_CLOSE(f.S(1), 2.0, 1e-9);
    CHECK_CLOSE(f.S(2), 0.5, 1e-8);

    double err_u = orthogonality_error(f.U);
    double err_v = orthogonality_error(f.V);
    ctx.state("orth_err_U", std::to_string(err_u));
    ctx.state("orth_err_V", std::to_string(err_v));
    CHECK(err_u < 1e-10);
    CHECK(err_v < 1e-10);

    double exact = (prob.A - f.approximate()).norm();
    ctx.state("exact_residual_recomputed", std::to_string(exact));
    CHECK(exact < 1e-7);
    CHECK(f.diag.exact_residual_valid);
    CHECK_CLOSE(f.diag.exact_residual_frob, exact, 1e-12);

    SvdReference ref = full_svd_reference(prob.A);
    ctx.state("ref_sigma4", std::to_string(ref.sigmas(3)));
    CHECK(ref.sigmas(3) < 1e-10);
    CHECK_CLOSE(f.diag.reference_tail_frob, 0.0, 0.0, 1e-9);

    ctx.judge("fixed analytic singular values recovered; U,V orthonormal "
              "(|U^TU-I|<1e-10); exact residual <1e-7; probe kept separate");
}

// 2) Small spectral gap: sigma_3 / sigma_4 ~= 2.5%. With only a few power
// iterations recovery near the gap is imperfect; assert the documented
// approximation behaviour with explicit bounds rather than pretending exactness.
TEST(numeric_small_spectral_gap) {
    using namespace rlmf;
    Vector sigmas = small_gap_sigmas(12, /*gap_at*/ 4, /*gap_ratio*/ 0.025,
                                     /*decay*/ 0.85);
    LowRankProblem prob = make_low_rank(120, 90, sigmas, 0xB2B2B2B2ULL);

    FactorizeConfig cfg;
    cfg.target_rank = 4;
    cfg.oversampling = 10;
    cfg.power_iters = 6;
    cfg.seed = 0x243f6a8885a308d3ULL;

    auto r = randomized_factorize(prob.A, cfg);
    CHECK(r.ok());
    if (!r.ok()) { ctx.judge("factorize failed: " + r.error().message); return; }
    const Factorization& f = r.value();
    fill_record(ctx, prob.A, cfg, f);

    ctx.state("sigmas_recovered", sigmas_str(f.S));
    ctx.state("sigmas_true", sigmas_str(sigmas.head(4)));
    ctx.state("sigma5_true", std::to_string(sigmas(4)));

    // Top singular values (far above the gap) recovered tightly.
    CHECK_CLOSE(f.S(0), sigmas(0), 1e-8);
    CHECK_CLOSE(f.S(1), sigmas(1), 1e-8);
    // The gap-neighboring sigma_4: allow a looser but explicit error bound.
    double rel_err_4 = std::abs(f.S(3) - sigmas(3)) / sigmas(3);
    ctx.state("rel_err_sigma4", std::to_string(rel_err_4));
    CHECK(rel_err_4 < 0.05);
    // Recovered sigma_4 must still be separated from the discarded scale.
    CHECK(f.S(3) > 3.0 * sigmas(4));

    // Orthogonality must hold even in the hard case.
    double err_u = orthogonality_error(f.U);
    double err_v = orthogonality_error(f.V);
    ctx.state("orth_err_U", std::to_string(err_u));
    ctx.state("orth_err_V", std::to_string(err_v));
    CHECK(err_u < 1e-9);
    CHECK(err_v < 1e-9);

    // Exact residual must sit between the SVD optimum floor and an explicit
    // upper bound derived from the discarded energy.
    const double optimal = f.diag.reference_tail_frob;
    const double exact = f.diag.exact_residual_frob;
    ctx.state("exact_residual", std::to_string(exact));
    ctx.state("optimal_tail", std::to_string(optimal));
    CHECK(exact >= optimal * (1.0 - 1e-8)); // cannot beat SVD optimum
    CHECK(exact <= 2.5 * optimal + 1e-9);   // but stays in the same regime

    // Probe estimate vs exact: same order of magnitude, but explicitly not
    // asserted equal (it is an estimator).
    double probe_ratio = f.diag.est_residual_frob /
                         std::max(exact, 1e-300);
    ctx.state("probe_to_exact_ratio", std::to_string(probe_ratio));
    CHECK(probe_ratio > 0.2);
    CHECK(probe_ratio < 5.0);

    ctx.judge("small-gap case: top sigma exact to 1e-8, sigma4 within 5%, "
              "orthogonality <1e-9, exact residual within [opt, 2.5*opt]; "
              "probe only order-of-magnitude (not exact)");
}

// 3) Rank-deficient: true rank r < min(m,n); achieved numerical rank must be r.
TEST(numeric_rank_deficient) {
    using namespace rlmf;
    LowRankProblem prob =
        make_rank_deficient(50, 40, /*true_rank*/ 5, 0xC3C3C3C3ULL);

    FactorizeConfig cfg;
    cfg.target_rank = 8; // ask for MORE than the true rank
    cfg.oversampling = 10;
    cfg.power_iters = 3;
    cfg.seed = 0x0123456789abcdefULL;

    auto r = randomized_factorize(prob.A, cfg);
    CHECK(r.ok());
    if (!r.ok()) { ctx.judge("factorize failed: " + r.error().message); return; }
    const Factorization& f = r.value();
    fill_record(ctx, prob.A, cfg, f);

    ctx.state("achieved_rank", std::to_string(f.diag.achieved_rank));
    ctx.state("true_rank", "5");
    ctx.state("sigmas_recovered", sigmas_str(f.S));
    ctx.state("sigmas_true", sigmas_str(prob.sigmas));

    CHECK(f.diag.achieved_rank == 5);
    for (long i = 0; i < 5; ++i)
        CHECK_CLOSE(f.S(i), prob.sigmas(i), 1e-7);

    double exact = (prob.A - f.approximate()).norm();
    ctx.state("exact_residual", std::to_string(exact));
    CHECK(exact < 1e-8);
    CHECK(orthogonality_error(f.U) < 1e-10);
    CHECK(orthogonality_error(f.V) < 1e-10);

    ctx.judge("rank-5 input with k=8 yields numerical rank 5 (no spurious "
              "directions), true singular values recovered, exact residual<1e-8");
}

// 4) Exact zero matrix: valid rank-0 factorization, no crash, zero residual.
TEST(numeric_zero_matrix) {
    using namespace rlmf;
    Matrix z = make_zero(30, 20);

    FactorizeConfig cfg;
    cfg.target_rank = 4;
    cfg.oversampling = 5;
    cfg.power_iters = 2;
    cfg.seed = 0xdeadbeefcafe1234ULL;

    auto r = randomized_factorize(z, cfg);
    CHECK(r.ok());
    if (!r.ok()) { ctx.judge("factorize failed: " + r.error().message); return; }
    const Factorization& f = r.value();
    fill_record(ctx, z, cfg, f);

    ctx.state("achieved_rank", std::to_string(f.diag.achieved_rank));
    ctx.state("frob_A", std::to_string(f.diag.frob_A));
    ctx.state("exact_residual", std::to_string(f.diag.exact_residual_frob));
    ctx.state("est_residual", std::to_string(f.diag.est_residual_frob));

    CHECK(f.diag.achieved_rank == 0);
    CHECK(f.U.cols() == 0 && f.V.cols() == 0 && f.S.size() == 0);
    CHECK(f.diag.frob_A == 0.0);
    CHECK(f.diag.exact_residual_valid);
    CHECK(f.diag.exact_residual_frob == 0.0);
    CHECK(f.diag.est_residual_frob < 1e-12);

    ctx.judge("zero matrix produces a valid rank-0 factorization; exact "
              "residual is exactly 0; probe ~0; no ComputationFailed raised");
}

// 5) Determinism: identical (seed, config) replays produce identical factors.
TEST(numeric_replay_is_bit_reproducible) {
    using namespace rlmf;
    Vector sigmas(6);
    sigmas << 9, 7, 4, 2.5, 1.2, 0.3;
    LowRankProblem prob = make_low_rank(80, 60, sigmas, 0xD4D4D4D4ULL);

    FactorizeConfig cfg;
    cfg.target_rank = 6;
    cfg.oversampling = 12;
    cfg.power_iters = 4;
    cfg.seed = 0x5555aaaa5555aaaaULL;

    auto r1 = randomized_factorize(prob.A, cfg);
    auto r2 = randomized_factorize(prob.A, cfg);
    CHECK(r1.ok() && r2.ok());
    if (r1.ok() && r2.ok()) {
        const auto& a = r1.value();
        const auto& b = r2.value();
        fill_record(ctx, prob.A, cfg, a);
        double max_du = (a.U - b.U).cwiseAbs().maxCoeff();
        double max_ds = (a.S - b.S).cwiseAbs().maxCoeff();
        double max_dv = (a.V - b.V).cwiseAbs().maxCoeff();
        ctx.state("max_dU", std::to_string(max_du));
        ctx.state("max_dS", std::to_string(max_ds));
        ctx.state("max_dV", std::to_string(max_dv));
        CHECK(max_du == 0.0);
        CHECK(max_ds == 0.0);
        CHECK(max_dv == 0.0);
        ctx.judge("two replays with identical seed/config are bit-identical "
                  "(fixed mt19937_64 + Box-Muller stream)");
    }
}
