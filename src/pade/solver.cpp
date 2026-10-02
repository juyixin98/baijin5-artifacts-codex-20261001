#include "pade/solver.hpp"
#include "pade/logging.hpp"
#include "pade/polynomial.hpp"
#include <Eigen/SVD>
#include <algorithm>
#include <cmath>
#include <limits>
#include <sstream>

namespace pade {

using Matrix = Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic>;

namespace {

bool finiteVec(const Vector& v) {
    for (int i = 0; i < v.size(); ++i)
        if (!std::isfinite(v[i])) return false;
    return true;
}

// Toeplitz block A[i][j] = c_{m+i-j} (c_k=0 for k<0); b[i] = -c_{m+1+i}.
Matrix buildA(const std::vector<Real>& c, int m, int n) {
    Matrix A(n, n);
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < n; ++j) {
            const int k = m + i - j;
            A(i, j) = (k >= 0 && k < static_cast<int>(c.size())) ? c[k] : Real(0);
        }
    return A;
}

Vector buildB(const std::vector<Real>& c, int m, int n) {
    Vector b(n);
    for (int i = 0; i < n; ++i) b[i] = -c[m + i + 1];
    return b;
}

// From C Q = P: p_k = c_k + sum_{j=1..min(k,n)} q_j c_{k-j}.
Vector recoverP(const std::vector<Real>& c, const Vector& q, int m, int /*n*/) {
    Vector p(m + 1);
    for (int k = 0; k <= m; ++k) {
        Real s = c[k];
        for (int j = 1; j < q.size() && j <= k; ++j) s += q[j] * c[k - j];
        p[k] = s;
    }
    return p;
}

// Term-wise Pade residual D_k = (C Q)_k - p_k for k = 0..needed-1.
// The match order is exactly the first k for which D_k != 0. This is the
// direct convolution identity; no deconvolution or rescaling is involved.
Vector computeResidual(const std::vector<Real>& c, const Vector& p, const Vector& q,
                       int needed, std::string& problem) {
    problem.clear();
    const int have = static_cast<int>(c.size());
    const int len = std::min(needed, have);
    if (len < needed) {
        std::ostringstream os;
        os << "note: " << have << " residual coefficients available, " << needed
           << " requested; extra-term evidence beyond the contract is partial";
        problem = os.str();
    }
    Vector d(len);
    for (int k = 0; k < len; ++k) {
        Real cq = 0;
        for (int j = 0; j < q.size() && j <= k; ++j) {
            const int ci = k - j;
            if (ci < have) cq += q[j] * c[ci];
        }
        const Real pk = k < p.size() ? p[k] : Real(0);
        d[k] = cq - pk;
    }
    return d;
}

int countMatched(const Vector& r, const std::vector<Real>& c, Real tol) {
    int matched = 0;
    for (int k = 0; k < r.size(); ++k) {
        const Real scale = Real(1) + std::fabs(k < static_cast<int>(c.size()) ? c[k] : Real(0));
        if (std::fabs(r[k]) > tol * scale) break;
        ++matched;
    }
    return matched;
}

// Shared post-processing: residual verification + common factor diagnosis.
// The unreduced p/q in rep are never modified by this routine.
void finalizeDiagnostics(SolveReport& rep, const std::vector<Real>& c,
                         const SolveOptions& opt, const std::string& trunc_problem) {
    const int need = rep.required_series_len + opt.residual_extra_terms;
    std::string problem = trunc_problem;
    rep.residual = computeResidual(c, rep.p, rep.q, need, problem);
    rep.matched_terms = countMatched(rep.residual, c, opt.residual_tol);
    // Contract: the first m+n+1 residual coefficients must vanish.
    rep.residual_verified = rep.matched_terms >= rep.required_series_len;

    {
        std::ostringstream os;
        os << "residual matched_terms=" << rep.matched_terms << "/" << need
           << " required=" << rep.required_series_len
           << " first_nonzero=";
        bool shown = false;
        for (int k = 0; k < rep.residual.size(); ++k)
            if (std::fabs(rep.residual[k]) > opt.residual_tol *
                    (Real(1) + std::fabs(c[k]))) { os << k << "(" << static_cast<double>(rep.residual[k]) << ")"; shown = true; break; }
        if (!shown) os << "none";
        log::info(rep.run_id, "residual:check", os.str());
    }

    if (rep.q0_normalized && !rep.residual_verified && rep.status == StatusCode::Ok) {
        rep.status = StatusCode::ResidualMismatch;
        rep.reason = "normalized solution failed term-wise residual: matched " +
                    std::to_string(rep.matched_terms) + " of " +
                    std::to_string(rep.required_series_len);
        log::error(rep.run_id, "residual:check", rep.reason);
    } else if (!problem.empty()) {
        log::info(rep.run_id, "residual:note", problem);
    }

    // Common factor detection on the unreduced local definition.
    if (opt.remove_common_factor) {
        try {
            int iters = 0;
            Vector g = poly::gcdMonic(rep.p, rep.q, opt.gcd_tol, iters);
            const int gd = static_cast<int>(g.size()) - 1;
            rep.gcd_degree = gd;
            rep.gcd_coeffs = g;
            if (gd > 0) {
                auto dp = poly::divide(rep.p, g, opt.gcd_tol);
                auto dq = poly::divide(rep.q, g, opt.gcd_tol);
                rep.p_reduced = dp.quotient;
                rep.q_reduced = dq.quotient;
                rep.common_factor_removed = true;
                Vector rp = dp.remainder, rq = dq.remainder;
                Real np = 0, nq = 0;
                for (int i = 0; i < rp.size(); ++i) np = std::max(np, std::fabs(rp[i]));
                for (int i = 0; i < rq.size(); ++i) nq = std::max(nq, std::fabs(rq[i]));
                rep.reduced_residual_norm = std::max(np, nq);
                std::ostringstream os;
                os << "common factor degree=" << gd << " gcd=" << to_string(g)
                   << " reduced p=" << to_string(rep.p_reduced)
                   << " reduced q=" << to_string(rep.q_reduced)
                   << " divide_remainder_inf=" << static_cast<double>(rep.reduced_residual_norm)
                   << " (unreduced [" << rep.m << "/" << rep.n << "] retained)";
                log::info(rep.run_id, "gcd:eliminate", os.str());
            } else {
                log::info(rep.run_id, "gcd:eliminate", "no non-trivial common factor");
            }
        } catch (const std::exception& e) {
            // GCD is diagnostic; failure must not masquerade as success.
            log::warn(rep.run_id, "gcd:eliminate", std::string("common factor diagnosis failed: ") + e.what());
            rep.gcd_degree = -2;
        }
    }
}

} // namespace

SolveReport solvePade(const SeriesRequest& req) {
    SolveReport rep;
    rep.run_id = log::makeRunId();
    rep.m = req.m;
    rep.n = req.n;
    rep.required_series_len = req.m + req.n + 1;
    const SolveOptions& opt = req.options;

    auto fail = [&](StatusCode code, const std::string& step, const std::string& why) {
        rep.status = code;
        rep.reason = why;
        log::error(rep.run_id, step, why);
        return rep;
    };

    if (req.m < 0 || req.n < 0)
        return fail(StatusCode::InvalidArgument, "validate", "orders m,n must be non-negative");
    if (static_cast<int>(req.coefficients.size()) < rep.required_series_len) {
        std::ostringstream os;
        os << "need " << rep.required_series_len << " coefficients for [" << req.m
           << "/" << req.n << "], got " << req.coefficients.size();
        return fail(StatusCode::InvalidArgument, "validate", os.str());
    }

    {
        std::ostringstream os;
        os << "m=" << req.m << " n=" << req.n
           << " series_len=" << req.coefficients.size()
           << " rank_tol_factor=" << static_cast<double>(opt.rank_tol_factor);
        log::info(rep.run_id, "solve:start", os.str());
    }

    const std::vector<Real>& c = req.coefficients;

    // n == 0: polynomial approximant [m/0].
    if (req.n == 0) {
        rep.p = Vector(req.m + 1);
        for (int k = 0; k <= req.m; ++k) rep.p[k] = c[k];
        rep.q = Vector::Ones(1);
        rep.q0_normalized = true;
        rep.matrix_rank = 0;
        rep.condition_number = 1;
        rep.status = StatusCode::Ok;
        rep.reason = "polynomial [m/0] approximant";
        log::info(rep.run_id, "solve:branch", "n=0 polynomial branch");
        finalizeDiagnostics(rep, c, opt, "");
        return rep;
    }

    Matrix A = buildA(c, req.m, req.n);
    Vector b = buildB(c, req.m, req.n);

    Eigen::JacobiSVD<Matrix> svd(A, Eigen::ComputeFullU | Eigen::ComputeFullV);
    const Vector sv = svd.singularValues();
    rep.singular_values = sv;
    if (!finiteVec(sv) || sv.size() == 0)
        return fail(StatusCode::NumericalFailure, "svd", "SVD produced non-finite singular values");

    const Real smax = sv[0];
    const Real threshold = opt.rank_tol_factor * std::numeric_limits<Real>::epsilon() *
                           std::max(Real(1), smax) * static_cast<Real>(req.n);
    int rank = 0;
    for (int i = 0; i < sv.size(); ++i) if (sv[i] > threshold) ++rank;
    rep.matrix_rank = rank;
    rep.condition_number = smax / std::max(sv[std::max(0, rank - 1 > 0 ? rank - 1 : 0)], threshold);

    {
        std::ostringstream os;
        os << "Toeplitz " << req.n << "x" << req.n << " rank=" << rank
           << " sigma_max=" << static_cast<double>(smax)
           << " sigma_min=" << static_cast<double>(sv[sv.size() - 1])
           << " rank_threshold=" << static_cast<double>(threshold);
        log::info(rep.run_id, "svd:rank", os.str());
    }

    if (rank == req.n) {
        // Full rank: unique solution with q0 = 1.
        Vector qtail = svd.solve(b);
        if (!finiteVec(qtail))
            return fail(StatusCode::NumericalFailure, "solve", "non-finite denominator coefficients");
        rep.q = Vector(req.n + 1);
        rep.q[0] = 1;
        rep.q.tail(req.n) = qtail;
        rep.q0_normalized = true;
        rep.p = recoverP(c, rep.q, req.m, req.n);
        rep.status = StatusCode::Ok;
        rep.reason = "unique [m/n] approximant (full-rank Toeplitz block)";
        log::info(rep.run_id, "solve:branch", "full rank, q0=1 unique solve");
        finalizeDiagnostics(rep, c, opt, "");
        return rep;
    }

    // Rank deficient: decide consistency of A qtail = b by projection residual.
    const Vector Utb = svd.matrixU().transpose() * b;
    Real incons = 0;
    for (int i = rank; i < Utb.size(); ++i) incons = std::max(incons, std::fabs(Utb[i]));
    const Real bscale = std::max(Real(1), b.cwiseAbs().maxCoeff());
    const bool consistent = incons <= opt.rank_tol_factor * bscale;

    if (!consistent) {
        // q0 = 1 cannot be satisfied: block-Pade degeneracy.
        // Produce the homogeneous representative A qtail = 0 with q0 = 0,
        // retaining it purely for diagnosis.
        Matrix V = svd.matrixV();
        Vector qh(req.n + 1);
        qh[0] = 0;
        qh.tail(req.n) = V.col(V.cols() - 1);
        // Normalize so the largest coefficient is 1 for a stable representative.
        Real mx = 0;
        for (int i = 0; i < qh.size(); ++i) mx = std::max(mx, std::fabs(qh[i]));
        if (mx > 0) qh /= mx;
        rep.q = qh;
        rep.q0_normalized = false;
        rep.p = recoverP(c, rep.q, req.m, req.n);
        rep.status = StatusCode::DegenerateDenominatorConstant;
        std::ostringstream os;
        os << "q0=1 normalization infeasible: Toeplitz rank " << rank << " < " << req.n
           << " and augmented system inconsistent (inconsistency " << static_cast<double>(incons / bscale)
           << " of rhs scale); the Pade table is block-degenerate near [" << req.m << "/"
           << req.n << "], a smaller-diagonal approximant carries the matching order";
        rep.reason = os.str();
        log::error(rep.run_id, "solve:degenerate", rep.reason);
        // Residual check is still recorded (informational; q0=0 deconvolution).
        finalizeDiagnostics(rep, c, opt, "");
        // Degeneracy is the definitive category; do not let residual heuristics overwrite it.
        rep.status = StatusCode::DegenerateDenominatorConstant;
        return rep;
    }

    // Singular but consistent: minimum-norm solution with q0 = 1.
    Vector qtail = svd.solve(b);
    if (!finiteVec(qtail))
        return fail(StatusCode::NumericalFailure, "solve", "non-finite minimum-norm solution");
    rep.q = Vector(req.n + 1);
    rep.q[0] = 1;
    rep.q.tail(req.n) = qtail;
    rep.q0_normalized = true;
    rep.p = recoverP(c, rep.q, req.m, req.n);
    rep.status = StatusCode::RankDeficient;
    std::ostringstream os;
    os << "rank " << rank << " < " << req.n << " but consistent; minimum-norm q0=1 representative";
    rep.reason = os.str();
    log::warn(rep.run_id, "solve:rankdeficient", rep.reason);
    finalizeDiagnostics(rep, c, opt, "");
    // Keep RankDeficient category even when residual fully matches.
    if (rep.residual_verified) rep.status = StatusCode::RankDeficient;
    return rep;
}

EvalReport evaluate(const SolveReport& rep, Real x, Real pole_relative_tol) {
    EvalReport ev;
    ev.x = x;
    const Vector& q = rep.common_factor_removed ? rep.q_reduced : rep.q;
    const Vector& p = rep.common_factor_removed ? rep.p_reduced : rep.p;
    ev.used_reduced = rep.common_factor_removed;
    ev.denominator = poly::eval(q, x);
    ev.numerator = poly::eval(p, x);

    const Real qscale = poly::eval(q.cwiseAbs(), std::fabs(x));
    // Also evaluate the unreduced denominator: a canceled pole is still worth
    // warning about if the unreduced form sits on it.
    const Real q_unreduced = poly::eval(rep.q, x);
    const Real q_unred_scale = poly::eval(rep.q.cwiseAbs(), std::fabs(x));

    if (!std::isfinite(ev.numerator) || !std::isfinite(ev.denominator)) {
        ev.status = StatusCode::DenominatorNearZero;
        ev.reason = "non-finite polynomial value during evaluation";
        return ev;
    }
    if (std::fabs(ev.denominator) <= pole_relative_tol * std::max(Real(1), qscale)) {
        std::ostringstream os;
        os << "denominator " << static_cast<double>(ev.denominator) << " near zero at x="
           << static_cast<double>(x) << " (relative "
           << static_cast<double>(std::fabs(ev.denominator) / std::max(Real(1), qscale))
           << (ev.used_reduced ? ", reduced form" : "") << ")";
        ev.status = StatusCode::DenominatorNearZero;
        ev.reason = os.str();
        return ev;
    }
    ev.value = ev.numerator / ev.denominator;
    if (!std::isfinite(ev.value)) {
        ev.status = StatusCode::DenominatorNearZero;
        ev.reason = "quotient non-finite";
        return ev;
    }
    ev.status = StatusCode::Ok;
    if (rep.common_factor_removed &&
        std::fabs(q_unreduced) <= pole_relative_tol * std::max(Real(1), q_unred_scale)) {
        // Not a failure after cancellation, but expose the removable singularity.
        ev.reason = "evaluated reduced fraction through a removable singularity of the unreduced [m/n] form";
    }
    return ev;
}

} // namespace pade
