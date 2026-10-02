#include "rlmf/factorizer.hpp"

#include <Eigen/SVD>

#include <algorithm>
#include <cmath>
#include <new>
#include <sstream>

namespace rlmf {

namespace {

bool finite_matrix(const Matrix& a) { return a.allFinite(); }

// Gaussian probe estimate of ||R||_F^2: for w ~ N(0, I_n),
// E[w^T R^T R w] = ||R||_F^2. We average ||R w||^2 over t draws. The residual
// matrix R is never formed (only A*w and approx*w products).
double probe_residual_frob(const Matrix& a, const Matrix& approx,
                           std::uint64_t seed, int probes) {
    GaussianStream rng(seed);
    const Index n = a.cols();
    double acc = 0.0;
    for (int t = 0; t < probes; ++t) {
        Vector w(n);
        for (Index i = 0; i < n; ++i) w(i) = rng.next();
        Vector rw = a * w - approx * w;
        acc += rw.squaredNorm();
    }
    return std::sqrt(acc / std::max(probes, 1));
}

double numerical_zero_cutoff(const Matrix& a) {
    // singular values below this are treated as exact zero
    return std::numeric_limits<double>::epsilon() *
           static_cast<double>(std::max(a.rows(), a.cols()));
}

} // namespace

std::optional<Error> validate_request(const Matrix& a,
                                      const FactorizeConfig& cfg) {
    if (!finite_matrix(a))
        return Error{ErrorKind::InvalidArgument, "input.non_finite",
                     "input matrix contains NaN or Inf"};
    if (a.rows() == 0 || a.cols() == 0)
        return Error{ErrorKind::InvalidArgument, "input.empty",
                     "input matrix must have at least one row and column"};
    if (cfg.target_rank <= 0)
        return Error{ErrorKind::InvalidArgument, "rank.non_positive",
                     "target_rank must be >= 1"};
    if (cfg.oversampling < 0)
        return Error{ErrorKind::InvalidArgument, "oversampling.negative",
                     "oversampling must be >= 0"};
    if (cfg.power_iters < 0)
        return Error{ErrorKind::InvalidArgument, "power_iters.negative",
                     "power_iters must be >= 0"};
    const Index max_rank = std::min(a.rows(), a.cols());
    if (static_cast<Index>(cfg.target_rank) > max_rank)
        return Error{ErrorKind::InvalidArgument, "rank.out_of_range",
                     "target_rank exceeds min(rows, cols)"};
    if (!(cfg.rank_tol > 0.0))
        return Error{ErrorKind::InvalidArgument, "rank_tol.non_positive",
                     "rank_tol must be strictly positive"};
    if (static_cast<long long>(cfg.target_rank) + cfg.oversampling >
        static_cast<long long>(a.rows()))
        return Error{ErrorKind::InvalidArgument, "sketch.exceeds_rows",
                     "target_rank + oversampling exceeds row count"};
    return std::nullopt;
}

Result<OrthonormResult> RandomRangeSketch::initial(const Matrix& a, int ell) {
    try {
        GaussianStream rng(cfg_.seed);
        Matrix omega = rng.matrix(a.cols(), ell);
        Matrix y = a * omega;
        return Result<OrthonormResult>{orthonormalize(y, cfg_.rank_tol)};
    } catch (const std::bad_alloc&) {
        return Result<OrthonormResult>{
            Error{ErrorKind::ResourceExhausted, "allocation.sketch",
                  "memory allocation failed while building the random sketch"}};
    } catch (const std::exception& e) {
        return Result<OrthonormResult>{
            Error{ErrorKind::ComputationFailed, "sketch.failed",
                  std::string("range sketch failed: ") + e.what()}};
    }
}

Result<OrthonormResult> RandomRangeSketch::step(const Matrix& a,
                                                const Matrix& q) {
    try {
        // Reorthogonalized power iteration: z = orth(A^T Q), then y = A z,
        // then Q = orth(y). Both inner and outer QR passes constitute the
        // required reorthogonalization of the power method.
        Matrix z = Matrix(Eigen::HouseholderQR<Matrix>(a.transpose() * q)
                              .householderQ())
                       .leftCols(q.cols());
        Matrix y = a * z;
        OrthonormResult on = orthonormalize(y, cfg_.rank_tol);
        if (on.rank == 0)
            return Result<OrthonormResult>{
                Error{ErrorKind::ComputationFailed,
                      "power_iteration.collapsed",
                      "range basis collapsed to rank 0 during power iteration "
                      "(matrix is numerically zero)"}};
        return Result<OrthonormResult>{on};
    } catch (const std::bad_alloc&) {
        return Result<OrthonormResult>{
            Error{ErrorKind::ResourceExhausted, "allocation.power_step",
                  "memory allocation failed during a power iteration step"}};
    } catch (const std::exception& e) {
        return Result<OrthonormResult>{
            Error{ErrorKind::ComputationFailed, "power_step.failed",
                  std::string("power iteration step failed: ") + e.what()}};
    }
}

Result<Factorization> factorize_with_sketch(const Matrix& a,
                                            const FactorizeConfig& cfg,
                                            RangeSketch& sketch) {
    if (auto err = validate_request(a, cfg); err.has_value())
        return Result<Factorization>{*err};

    Factorization fac;
    FactorizeDiagnostics& diag = fac.diag;
    diag.requested_rank = cfg.target_rank;
    diag.sketch_cols = cfg.target_rank + cfg.oversampling;
    diag.frob_A = a.norm();

    auto first = sketch.initial(a, diag.sketch_cols);
    if (!first.ok())
        return Result<Factorization>{first.error()};
    Matrix q = first.value().q;
    diag.ortho_rank_Q = first.value().rank;

    // A zero (or numerically zero) range at the first sketch means the matrix
    // has no range to amplify; skip power steps and return the rank-0 result.
    for (int it = 0; it < cfg.power_iters && q.cols() > 0; ++it) {
        auto next = sketch.step(a, q);
        if (!next.ok())
            return Result<Factorization>{next.error()};
        q = next.value().q;
    }

    if (q.cols() == 0) {
        fac.U = Matrix(a.rows(), 0);
        fac.V = Matrix(a.cols(), 0);
        fac.S = Vector(0);
        diag.achieved_rank = 0;
        diag.rel_frob_approx = diag.frob_A == 0.0 ? 1.0 : 0.0;
        Matrix zero = Matrix::Zero(a.rows(), a.cols());
        diag.est_residual_frob =
            probe_residual_frob(a, zero, cfg.seed ^ 0x9e3779b9ULL, 16);
        if (cfg.compute_exact_residual) {
            diag.exact_residual_valid = true;
            diag.exact_residual_frob = diag.frob_A;
        }
        std::ostringstream os;
        os << "range basis rank is 0; A is numerically zero -> rank-0 "
              "factor; ESTIMATED residual = "
           << diag.est_residual_frob;
        diag.rationale = os.str();
        return Result<Factorization>{std::move(fac)};
    }

    try {
        // Project and take exact SVD of the small matrix B = Q^T A.
        Matrix b = q.transpose() * a;
        Eigen::JacobiSVD<Matrix> svd(b, Eigen::ComputeThinU | Eigen::ComputeThinV);
        const Index available = svd.singularValues().size();
        const Index want = std::min<Index>(cfg.target_rank, available);

        // Numerical-rank truncation: drop trailing singular values that are
        // zero to machine precision (rank-deficient inputs).
        const double cutoff =
            numerical_zero_cutoff(a) * std::max(svd.singularValues()(0), 1.0);
        Index r = 0;
        while (r < want && svd.singularValues()(r) > cutoff) ++r;

        Vector s = svd.singularValues().head(r);
        Matrix ub = svd.matrixU().leftCols(r);
        Matrix v = svd.matrixV().leftCols(r);
        Matrix u = q * ub;

        fac.U = std::move(u);
        fac.S = std::move(s);
        fac.V = std::move(v);
        diag.achieved_rank = static_cast<int>(r);

        Matrix approx = fac.approximate();
        diag.rel_frob_approx =
            diag.frob_A > 0.0 ? approx.norm() / diag.frob_A : 1.0;
        diag.est_residual_frob =
            probe_residual_frob(a, approx, cfg.seed ^ 0x9e3779b9ULL, 16);
        if (cfg.compute_exact_residual) {
            diag.exact_residual_valid = true;
            diag.exact_residual_frob = (a - approx).norm();
        }
        if (cfg.compute_reference_tail) {
            Eigen::BDCSVD<Matrix> full(
                a, Eigen::ComputeThinU | Eigen::ComputeThinV);
            const Vector& all_s = full.singularValues();
            double tail_sq = 0.0;
            for (Index i = r; i < all_s.size(); ++i)
                tail_sq += all_s(i) * all_s(i);
            diag.reference_valid = true;
            diag.reference_tail_frob = std::sqrt(tail_sq);
        }

        std::ostringstream os;
        os << "randomized range sketch ell=" << diag.sketch_cols
           << " (k=" << cfg.target_rank << ", p=" << cfg.oversampling
           << "), q=" << cfg.power_iters << ", seed=0x" << std::hex
           << cfg.seed << std::dec << "; achieved rank " << r
           << "; RESIDUAL PROBE IS AN ESTIMATE (" << diag.est_residual_frob
           << ")";
        if (diag.exact_residual_valid)
            os << "; exact residual " << diag.exact_residual_frob;
        if (diag.reference_valid)
            os << "; SVD-optimal tail (independent reference) "
               << diag.reference_tail_frob;
        os << "; method is approximate and not claimed exact-optimal.";
        diag.rationale = os.str();
    } catch (const std::bad_alloc&) {
        return Result<Factorization>{
            Error{ErrorKind::ResourceExhausted, "allocation.svd",
                  "memory allocation failed while computing the projected SVD"}};
    } catch (const std::exception& e) {
        return Result<Factorization>{
            Error{ErrorKind::ComputationFailed, "svd.failed",
                  std::string("projected SVD failed: ") + e.what()}};
    }

    return Result<Factorization>{std::move(fac)};
}

Result<Factorization> randomized_factorize(const Matrix& a,
                                           const FactorizeConfig& cfg) {
    RandomRangeSketch sketch(cfg);
    return factorize_with_sketch(a, cfg, sketch);
}

} // namespace rlmf
