#include "test_framework.hpp"

#include "rlmf/errors_explained.hpp"
#include "rlmf/factorizer.hpp"
#include "rlmf/factorizer_session.hpp"
#include "rlmf/fixtures.hpp"

#include <sstream>

// Lifecycle: Configured -> InputReady -> Computed -> Consumed; every out-of
// -order call must be reported as StateConflict with a specific code.
TEST(contract_session_lifecycle) {
    using namespace rlmf;
    FactorizerSession session;

    // compute before input
    auto early = session.compute();
    CHECK_ERROR_KIND(early, StateConflict);
    CHECK_ERROR_CODE(early, "session.no_input");

    // take before compute
    auto early_take = session.take();
    CHECK_ERROR_KIND(early_take, StateConflict);
    CHECK_ERROR_CODE(early_take, "session.not_computed");

    FactorizeConfig cfg;
    cfg.target_rank = 2;
    cfg.oversampling = 4;
    cfg.power_iters = 1;
    CHECK(!session.set_config(cfg));

    Vector sig(2);
    sig << 3, 1;
    LowRankProblem p = make_low_rank(20, 15, sig, 0x7777ULL);
    CHECK(!session.set_input(p.A));

    // config locked after input
    FactorizeConfig other = cfg;
    other.target_rank = 3;
    Error lock = session.set_config(other);
    CHECK(lock.kind == rlmf::ErrorKind::StateConflict);
    CHECK(lock.code == "session.config_locked");

    auto r1 = session.compute();
    CHECK(r1.ok());

    // second compute is a state conflict, not a re-run
    auto r2 = session.compute();
    CHECK_ERROR_KIND(r2, StateConflict);
    CHECK_ERROR_CODE(r2, "session.already_computed");

    auto out = session.take();
    CHECK(out.ok());

    // double take is a conflict
    auto out2 = session.take();
    CHECK_ERROR_KIND(out2, StateConflict);
    CHECK_ERROR_CODE(out2, "session.not_computed");

    // after reset the session is reusable
    session.reset(cfg);
    CHECK(session.phase() == FactorizerSession::Phase::Configured);
    CHECK(!session.set_input(p.A));
    auto r3 = session.compute();
    CHECK(r3.ok());

    ctx.judge("session enforces Configured->InputReady->Computed->Consumed; "
              "wrong-phase calls return distinct StateConflict codes");
}

// Error explanation must keep estimated / exact / optimal quantities apart.
TEST(contract_error_explanation_distinguishes_quantities) {
    using namespace rlmf;
    Vector sig(4);
    sig << 6, 3, 0.8, 0.1;
    LowRankProblem p = make_low_rank(50, 35, sig, 0x8888ULL);

    FactorizeConfig cfg;
    cfg.target_rank = 2;
    cfg.oversampling = 8;
    cfg.power_iters = 2;
    auto r = randomized_factorize(p.A, cfg);
    CHECK(r.ok());
    if (!r.ok()) { ctx.judge(r.error().message); return; }

    ErrorExplanation ex = explain(r.value());
    ctx.state("headline", ex.headline);
    CHECK(ex.ok);
    bool mentions_estimate = ex.headline.find("ESTIMATED") != std::string::npos;
    bool mentions_exact = ex.headline.find("EXACT") != std::string::npos;
    bool mentions_tail = ex.headline.find("SVD-optimal") != std::string::npos;
    CHECK(mentions_estimate);
    CHECK(mentions_exact);
    CHECK(mentions_tail);
    CHECK(!ex.evidence.empty());

    // numerical ordering: exact residual >= SVD optimal tail (impossible to beat)
    CHECK(r.value().diag.exact_residual_frob + 1e-10 >=
          r.value().diag.reference_tail_frob);

    // explaining an error produces a failed explanation with the kind
    Error e{ErrorKind::InvalidArgument, "rank.non_positive", "k=0"};
    ErrorExplanation eb = explain(e);
    CHECK(!eb.ok);
    CHECK(eb.headline.find("InvalidArgument") != std::string::npos);

    ctx.judge("explanation labels probe as ESTIMATED, residual as EXACT and "
              "tail as SVD-optimal; error explanations carry the failure kind");
}

// Oversampling / seed are honored and fixed defaults match the contract.
TEST(contract_fixed_defaults) {
    using namespace rlmf;
    FactorizeConfig cfg;
    CHECK(cfg.oversampling == 10);
    CHECK(cfg.seed == 0x243f6a8885a308d3ULL);
    CHECK(cfg.power_iters == 2);
    CHECK(cfg.compute_exact_residual == true);
    ctx.judge("default oversampling=10, fixed seed and q=2 per contract");
}
