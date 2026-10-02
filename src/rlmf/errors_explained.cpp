#include "rlmf/errors_explained.hpp"

#include <sstream>

namespace rlmf {

bool within(double observed, double expected, double rel_tol,
            double abs_tol) noexcept {
    const double scale = std::abs(expected);
    return std::abs(observed - expected) <= rel_tol * scale + abs_tol;
}

ErrorExplanation explain(const Factorization& fac) noexcept {
    ErrorExplanation ex;
    const auto& d = fac.diag;
    std::ostringstream head;
    head << "rank " << d.achieved_rank << "/" << d.requested_rank
         << " factorization; ESTIMATED residual " << d.est_residual_frob;
    if (d.exact_residual_valid)
        head << "; EXACT residual " << d.exact_residual_frob;
    if (d.reference_valid)
        head << "; SVD-optimal tail " << d.reference_tail_frob;
    ex.headline = head.str();

    auto add = [&](const char* name, bool ok, std::string obs,
                   std::string exp) {
        ex.evidence.push_back(Evidence{name, std::move(obs), std::move(exp), ok});
    };

    add("rank.requested", d.achieved_rank == d.requested_rank ||
                              d.achieved_rank == 0,
        std::to_string(d.achieved_rank), std::to_string(d.requested_rank));

    if (d.exact_residual_valid && d.reference_valid) {
        // Randomized method must be no worse than a small multiple of the
        // optimal truncation; being strictly better is impossible.
        const bool plausible = d.exact_residual_frob >=
                                   d.reference_tail_frob * (1.0 - 1e-9) &&
                               std::isfinite(d.exact_residual_frob);
        add("residual.vs_optimal_floor", plausible,
            std::to_string(d.exact_residual_frob),
            ">=" + std::to_string(d.reference_tail_frob));
    }
    ex.ok = true;
    return ex;
}

ErrorExplanation explain(const Error& err) noexcept {
    ErrorExplanation ex;
    ex.ok = false;
    std::ostringstream os;
    os << error_kind_name(err.kind) << ": " << err.message;
    ex.headline = os.str();
    ex.evidence.push_back(Evidence{"error.kind",
                                   error_kind_name(err.kind), "None", false});
    ex.evidence.push_back(
        Evidence{"error.code", err.code, "no error", false});
    return ex;
}

} // namespace rlmf
