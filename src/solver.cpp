#include "pade/solver.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

#include <Eigen/SVD>

#include "pade/polynomial.hpp"

namespace pade {

using Eigen::MatrixXd;
using Eigen::VectorXd;

namespace {

Real infNorm(const std::vector<Real>& v) {
    Real m = 0.0;
    for (Real x : v) m = std::max(m, std::abs(x));
    return m;
}

bool allFinite(const std::vector<Real>& v) {
    return std::all_of(v.begin(), v.end(),
                       [](Real x) { return std::isfinite(x); });
}

} // namespace

ResidualReport verifyResiduals(const std::vector<Real>& coeffs,
                               const std::vector<Real>& numerator,
                               const std::vector<Real>& denominator,
                               int m, int n) {
    ResidualReport rep;
    rep.checked_from = 0;
    rep.checked_through = m + n;
    rep.tolerance = Real(1e-10);
    int have = static_cast<int>(coeffs.size());

    // Term-by-term residual r_k = (C*B)_k - a_k,  where
    // (C*B)_k = sum_{j=0..min(k,n)} c_{k-j} b_j  (c_t = 0 for t < 0).
    // Pade matching order <=> r_0 = ... = r_{m+n} = 0.
    bool allMatch = true;
    rep.residual.assign(rep.checked_through + 1, Real(0));
    auto cb = [&](int k) {
        Real v = 0.0;
        for (int j = 0; j <= std::min(k, n); ++j) {
            int t = k - j;
            Real ct = (t < have) ? coeffs[t] : Real(0);
            v += ct * denominator[j];
        }
        return v;
    };
    for (int k = 0; k <= rep.checked_through; ++k) {
        Real conv = cb(k);
        Real ak = (k <= m && k < static_cast<int>(numerator.size()))
                      ? numerator[k] : Real(0);
        Real r = conv - ak;
        rep.residual[k] = r;
        Real scale = Real(1) + std::abs(conv) + std::abs(ak);
        rep.max_abs_residual = std::max(rep.max_abs_residual, std::abs(r));
        if (std::abs(r) > rep.tolerance * scale) allMatch = false;
    }
    // First uncontrolled term r_{m+n+1}: generically nonzero; needs c_{m+n+1}.
    if (have > m + n + 1) {
        rep.next_term_residual = cb(m + n + 1);
        rep.next_term_available = true;
    }
    rep.matches_to_order = allMatch;
    return rep;
}

PadeResult padeApproximate(const std::vector<Real>& coeffs,
                           const Options& opts) {
    PadeResult res;
    res.requested_m = opts.numerator_order;
    res.requested_n = opts.denominator_order;

    auto fail = [&](StatusCode code, std::string msg) {
        res.status = code;
        res.message = std::move(msg);
        return res;
    };

    if (opts.numerator_order < 0 || opts.denominator_order < 0)
        return fail(StatusCode::kInvalidArgument,
                    "orders m,n must be non-negative");
    if (!allFinite(coeffs))
        return fail(StatusCode::kInvalidArgument,
                    "coefficients contain NaN/Inf");
    if (coeffs.empty())
        return fail(StatusCode::kInvalidArgument, "empty coefficient series");
    const int need = opts.numerator_order + opts.denominator_order + 1;
    if (static_cast<int>(coeffs.size()) < need)
        return fail(StatusCode::kInsufficientCoeffs,
                    "need at least m+n+1 = " + std::to_string(need) +
                        " coefficients, got " + std::to_string(coeffs.size()));

    const int m = opts.numerator_order;
    const int n = opts.denominator_order;
    res.near_pole_tol_used = opts.near_pole_tol;
    auto c = [&](int k) -> Real {
        return (k >= 0 && k < static_cast<int>(coeffs.size())) ? coeffs[k]
                                                               : Real(0);
    };

    // Denominator equations for rows k = m+1 .. m+n:
    //   sum_{j=0..n} c_{k-j} b_j = 0  =>  B_{i,j} = c_{m+i-j}.
    MatrixXd B = MatrixXd::Zero(std::max(n, 1), n + 1);
    if (n > 0) {
        for (int i = 1; i <= n; ++i)
            for (int j = 0; j <= n; ++j) B(i - 1, j) = c(m + i - j);

    }

    // Classic C block used for b1..bn (informational rank).
    MatrixXd C = MatrixXd::Zero(std::max(n, 1), std::max(n, 1));
    if (n > 0) {
        for (int i = 1; i <= n; ++i)
            for (int j = 1; j <= n; ++j) C(i - 1, j - 1) = -c(m + i - j);
    }

    VectorXd bvec(n + 1);
    RankDiagnostics& rd = res.rank;
    rd.requested_n = n;

    if (n == 0) {
        bvec.setOnes(1);
        rd.numerical_rank = 0;
        rd.truncated_rank = 0;
        rd.nullity = 1;
        rd.sigma_max = rd.sigma_min = rd.effective_tol = 0.0;
    } else {
        Eigen::JacobiSVD<MatrixXd> svd(
            B, Eigen::ComputeFullV); // full V required for nullity>1
        const VectorXd& sv = svd.singularValues();
        rd.sigma_max = sv(0);
        rd.sigma_min = sv(sv.size() - 1);
        Real eps = std::numeric_limits<Real>::epsilon();
        rd.effective_tol =
            opts.singular_tol > Real(0)
                ? opts.singular_tol
                : Real(std::max(n, n + 1)) * eps * std::max(rd.sigma_max, Real(1));
        rd.numerical_rank = 0;
        for (int i = 0; i < sv.size(); ++i)
            if (sv(i) > rd.effective_tol) ++rd.numerical_rank;
        rd.nullity = (n + 1) - rd.numerical_rank;

        Eigen::JacobiSVD<MatrixXd> csvd(C, Eigen::ComputeThinU | Eigen::ComputeThinV);
        rd.truncated_rank = 0;
        Real ctol = Real(n) * eps * std::max(csvd.singularValues()(0), Real(1));
        for (int i = 0; i < csvd.singularValues().size(); ++i)
            if (csvd.singularValues()(i) > ctol) ++rd.truncated_rank;

        const MatrixXd& V = svd.matrixV();
        // Preserve the entire numerical null space for audit.
        for (int col = rd.numerical_rank; col <= n; ++col) {
            std::vector<Real> vec(n + 1);
            for (int k = 0; k <= n; ++k) vec[k] = V(k, col);
            rd.nullspace.push_back(vec);
        }
        // Deterministic canonical representative. Start from the whole
        // numerical null space Z and, for coordinates b_n,b_{n-1},...,b_1,
        // restrict to the affine/linear choice that zeroes that coordinate
        // whenever possible. This selects the lowest-degree normalizable
        // denominator (minimal Padé form) instead of an arbitrary SVD basis
        // vector, while the entire raw null space stays in rd.nullspace.
        MatrixXd Z = V(Eigen::all,
                       Eigen::seq(rd.numerical_rank, Eigen::last))
                         .eval();
        Real spaceTol = rd.effective_tol;
        for (int coord = n; coord >= 1; --coord) {
            if (Z.cols() < 2) break;
            VectorXd row = Z.row(coord).transpose();
            // Find a column able to pivot on this coordinate.
            int pivot = -1;
            Real prow = std::max(Real(1), row.cwiseAbs().maxCoeff());
            for (int j = 0; j < row.size(); ++j)
                if (std::abs(row(j)) > Real(1e-10) * prow) { pivot = j; break; }
            if (pivot < 0) continue; // coordinate already zero on this space
            MatrixXd Znew(n + 1, Z.cols() - 1);
            int w = 0;
            for (int j = 0; j < Z.cols(); ++j) {
                if (j == pivot) continue;
                Znew.col(w++) =
                    Z.col(j) - (row(j) / row(pivot)) * Z.col(pivot);
            }
            Z = Znew;
            (void)spaceTol;
        }
        // Among remaining vectors prefer the largest |b0| (normalizability).
        VectorXd choice = Z.col(0);
        Real bestAbs = -1.0;
        for (int j = 0; j < Z.cols(); ++j) {
            Real a0 = std::abs(Z(0, j));
            if (a0 > bestAbs) { bestAbs = a0; choice = Z.col(j); }
        }
        bvec = choice;
    }

    // ---- denominator constant normalization (b0 = 1) ----
    Real b0 = bvec(0);
    Real normTol = opts.gcd_tol * std::max(Real(1), bvec.cwiseAbs().maxCoeff());
    if (std::abs(b0) <= normTol) {
        // The defining normalization cannot be satisfied. Keep the unscaled
        // representative as the local definition (it still solves the block
        // equations), run reduction/verification, and flag the exact category.
        res.denominator_full.assign(n + 1, Real(0));
        for (int k = 0; k <= n; ++k) res.denominator_full[k] = bvec(k);
        res.normalized = false;
    } else {
        Real scale = Real(1) / b0;
        res.denominator_full.assign(n + 1, Real(0));
        for (int k = 0; k <= n; ++k)
            res.denominator_full[k] = scale * bvec(k);
        res.normalized = true;
    }

    std::vector<Real> numer(m + 1, Real(0));
    for (int k = 0; k <= m; ++k)
        for (int j = 0; j <= std::min(k, n); ++j)
            numer[k] += c(k - j) * res.denominator_full[j];
    res.numerator_full = std::move(numer);

    // ---- common-factor elimination (never mutates *_full) ----
    ReductionInfo& ri = res.reduction;
    Real polyScale =
        std::max(infNorm(res.numerator_full), infNorm(res.denominator_full));
    Real absZero = opts.gcd_tol * std::max(Real(1), polyScale);
    bool numerZero = infNorm(res.numerator_full) <= absZero;
    int shA = numerZero ? static_cast<int>(res.numerator_full.size())
                        : poly::leadingZeroShift(res.numerator_full, absZero);
    int shB = poly::leadingZeroShift(res.denominator_full, absZero);
    ri.monomial_shift = std::min(shA, shB);
    std::vector<Real> aStrip(res.numerator_full.begin() +
                                 std::min(ri.monomial_shift,
                                          static_cast<int>(res.numerator_full.size()) - 1),
                             res.numerator_full.end());
    std::vector<Real> bStrip(res.denominator_full.begin() + ri.monomial_shift,
                             res.denominator_full.end());
    if (numerZero) {
        res.numerator_reduced = {Real(0)};
        res.denominator_reduced = poly::trim(bStrip, opts.gcd_tol);
    } else {
        aStrip = poly::trim(aStrip, opts.gcd_tol);
        bStrip = poly::trim(bStrip, opts.gcd_tol);
        poly::ApproxGcd g = poly::approximateGcd(aStrip, bStrip, opts.gcd_tol);
        ri.polynomial_gcd_degree = g.degree;
        ri.gcd_coeffs = g.gcd;
        res.numerator_reduced =
            g.degree > 0 ? poly::divideByMonic(aStrip, g.gcd) : aStrip;
        res.denominator_reduced =
            g.degree > 0 ? poly::divideByMonic(bStrip, g.gcd) : bStrip;
        res.numerator_reduced =
            poly::trim(res.numerator_reduced, opts.gcd_tol);
        res.denominator_reduced =
            poly::trim(res.denominator_reduced, opts.gcd_tol);
    }
    ri.reduced_m = static_cast<int>(res.numerator_reduced.size()) - 1;
    ri.reduced_n = static_cast<int>(res.denominator_reduced.size()) - 1;

    // ---- order matching verified term by term on FULL local definition ----
    res.residual = verifyResiduals(coeffs, res.numerator_full,
                                   res.denominator_full, m, n);

    if (!res.normalized) {
        res.status = StatusCode::kNormalizationImpossible;
        res.message =
            "b0 ~ 0 on the null-space representative: |b0|=" +
            std::to_string(std::abs(b0)) + " tol=" + std::to_string(normTol) +
            "; requested [" + std::to_string(m) + "/" + std::to_string(n) +
            "] has no Pade form with normalized constant term (raw "
            "homogeneous solution and reduction retained)";
        return res;
    }

    if (rd.nullity > 1) {
        res.status = StatusCode::kRankDeficient;
        res.message = "numerical nullity " + std::to_string(rd.nullity) +
                      " at [" + std::to_string(m) + "/" + std::to_string(n) +
                      "]: denominator block rank deficient; normalized "
                      "best-effort representative returned";
        return res;
    }

    res.status = StatusCode::kOk;
    res.message = "Pade [" + std::to_string(m) + "/" + std::to_string(n) +
                  "] matched through order " + std::to_string(m + n) +
                  " (max|residual|=" +
                  std::to_string(res.residual.max_abs_residual) + ")";
    return res;
}

EvalResult evaluatePade(const PadeResult& r, Real x) {
    EvalResult e;
    e.x = x;
    const auto& num = r.numerator_reduced.empty() ? r.numerator_full
                                                  : r.numerator_reduced;
    const auto& den = r.denominator_reduced.empty() ? r.denominator_full
                                                    : r.denominator_reduced;
    e.denominator = poly::evaluate(den, x);
    e.value = poly::evaluate(num, x);
    Real dScale = infNorm(den);
    if (std::abs(e.denominator) <=
        r.near_pole_tol_used * std::max(Real(1), dScale)) {
        e.near_pole = true;
        e.status = StatusCode::kPoleEvaluated;
        e.message = "denominator near zero: |Q(x)|=" +
                    std::to_string(std::abs(e.denominator)) +
                    " at x=" + std::to_string(x) +
                    "; value reported as numerator/denominator ratio is unstable";
        if (e.denominator == Real(0))
            e.value = std::numeric_limits<Real>::infinity();
        else
            e.value = e.value / e.denominator;
        return e;
    }
    e.value /= e.denominator;
    e.status = StatusCode::kOk;
    e.message = "ok";
    return e;
}

} // namespace pade
