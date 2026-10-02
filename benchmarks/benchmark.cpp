// Independent benchmark for the randomized low-rank factorization.
//
// It measures the randomized kernel against an INDEPENDENT baseline (Eigen
// BDCSVD full decomposition computed here, not inside the core library) on
// local synthetic matrices. Output is CSV written to stdout / --csv file.
// The benchmark does not assert quality; it reports timing + the three error
// quantities so trade-offs are visible.
#include "rlmf/fixtures.hpp"
#include "rlmf/factorizer.hpp"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

using namespace rlmf;
using clk = std::chrono::steady_clock;

static double seconds_since(clk::time_point t0) {
    return std::chrono::duration<double>(clk::now() - t0).count();
}

// Independent baseline: full BDCSVD of A, then truncate to rank k. Kept in
// this translation unit so the benchmark is independent of the core.
struct BaselineResult {
    double time_s;
    double residual_frob;
    double tail_frob; // optimal rank-k tail sqrt(sum_{i>k} sigma_i^2)
};
BaselineResult full_svd_baseline(const Matrix& a, int k) {
    BaselineResult out{};
    auto t0 = clk::now();
    Eigen::BDCSVD<Matrix> svd(a, Eigen::ComputeThinU | Eigen::ComputeThinV);
    Matrix Uk = svd.matrixU().leftCols(k);
    Matrix Vk = svd.matrixV().leftCols(k);
    Vector sk = svd.singularValues().head(k);
    Matrix approx = (Uk * sk.asDiagonal()) * Vk.transpose();
    out.time_s = seconds_since(t0);
    out.residual_frob = (a - approx).norm();
    double tail_sq = 0.0;
    for (long i = k; i < svd.singularValues().size(); ++i)
        tail_sq += svd.singularValues()(i) * svd.singularValues()(i);
    out.tail_frob = std::sqrt(tail_sq);
    return out;
}

static Matrix synth_matrix(int m, int n, int r, std::uint64_t tag) {
    Vector s(r);
    for (int i = 0; i < r; ++i) s(i) = 10.0 * std::pow(0.85, i);
    return make_low_rank(m, n, s, tag).A;
}

int main(int argc, char** argv) {
    int repeats = 3;
    std::string csv_path;
    for (int i = 1; i + 1 < argc; ++i) {
        std::string k = argv[i];
        if (k == "--repeats") repeats = std::atoi(argv[i + 1]);
        else if (k == "--csv") csv_path = argv[i + 1];
    }

    struct Size { int m, n, k, p, q; };
    std::vector<Size> sizes = {
        {200, 150, 10, 10, 2},
        {500, 400, 20, 10, 2},
        {1000, 800, 30, 10, 3},
        {1500, 1000, 40, 12, 3},
    };

    std::ofstream csv;
    std::ostream* out = &std::cout;
    if (!csv_path.empty()) {
        csv.open(csv_path);
        out = &csv;
    }
    *out << std::setprecision(6);
    *out << "m,n,k,p,q,rep,rand_time_s,fullsvd_time_s,speedup,"
            "rand_est_residual,rand_exact_residual,svd_optimal_tail,"
            "exact_over_optimal\n";

    std::cerr << "Independent randomized-SVD benchmark (local synthetic data)\n";
    for (const auto& sz : sizes) {
        Matrix a = synth_matrix(sz.m, sz.n, sz.k + sz.p, 0xBEEFULL);
        FactorizeConfig cfg;
        cfg.target_rank = sz.k;
        cfg.oversampling = sz.p;
        cfg.power_iters = sz.q;
        cfg.seed = 0x243f6a8885a308d3ULL;
        // Timed core run does NOT compute the full-SVD reference (that is the
        // baseline's job and would hide the algorithmic speedup). The exact
        // residual is cheap and stays on so quality is reported per run.
        cfg.compute_reference_tail = false;
        cfg.compute_exact_residual = true;

        for (int rep = 0; rep < repeats; ++rep) {
            auto t0 = clk::now();
            auto r = randomized_factorize(a, cfg);
            double rand_t = seconds_since(t0);
            if (!r) {
                std::cerr << "factorization failed: " << r.error() << "\n";
                return 2;
            }
            // Baseline only computed once is cheaper, but measure per rep too.
            BaselineResult base = full_svd_baseline(a, sz.k);

            const auto& d = r.value().diag;
            double ratio = base.time_s / std::max(rand_t, 1e-9);
            double over_opt = base.tail_frob > 0
                                  ? d.exact_residual_frob / base.tail_frob
                                  : 1.0;
            *out << sz.m << ',' << sz.n << ',' << sz.k << ',' << sz.p << ','
                 << sz.q << ',' << rep << ',' << rand_t << ',' << base.time_s
                 << ',' << ratio << ',' << d.est_residual_frob << ','
                 << d.exact_residual_frob << ',' << base.tail_frob
                 << ',' << over_opt << '\n';
            out->flush();
            std::cerr << sz.m << "x" << sz.n << " k=" << sz.k << " rep=" << rep
                      << "  rand=" << rand_t << "s fullsvd=" << base.time_s
                      << "s speedup=" << ratio << "  exact/opt=" << over_opt
                      << "\n";
        }
    }
    if (!csv_path.empty())
        std::cerr << "CSV written to " << csv_path << "\n";
    return 0;
}
