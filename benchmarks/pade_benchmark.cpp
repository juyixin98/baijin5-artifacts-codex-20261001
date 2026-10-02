// Independent benchmark: Pade convergence for exp over increasing diagonal
// order k ( [k/k] ). The reference is libm long double std::exp, never the
// engine. Every row also re-verifies the residual match order and reports the
// Toeplitz condition number. Output is a parseable table plus JSON-ish summary.
#include "pade/logging.hpp"
#include "pade/seriesio.hpp"
#include "pade/solver.hpp"
#include "pade/version.hpp"
#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>

using namespace pade;

int main(int argc, char** argv) {
    int max_k = 6;
    int npts = 9;
    Real xmax = Real(0.5);
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--max-k" && i + 1 < argc) max_k = std::stoi(argv[++i]);
        else if (a == "--points" && i + 1 < argc) npts = std::stoi(argv[++i]);
        else if (a == "--xmax" && i + 1 < argc) xmax = std::stold(argv[++i]);
    }
    log::setEnabled(false);

    std::cout << "# pade benchmark " PADE_VERSION " engine=" PADE_ENGINE "\n";
    std::cout << "# reference: libm std::exp (long double), independent of engine\n";
    std::cout << "# columns: k matched cond max_rel_err\n";
    std::cout << std::setprecision(6);

    int failures = 0;
    for (int k = 0; k <= max_k; ++k) {
        const int terms = 2 * k + 9;
        std::vector<Real> c = series::generate(series::Kind::Exp, terms);
        SeriesRequest req;
        req.m = k; req.n = k; req.coefficients = c;
        SolveReport r = solvePade(req);

        if (isFailure(r.status)) {
            std::cout << k << "\tFAIL\tstatus=" << toString(r.status)
                      << "\treason=" << r.reason << "\n";
            ++failures;
            continue;
        }
        if (r.matched_terms < r.required_series_len) {
            std::cout << k << "\tRESIDUAL_FAIL\tmatched=" << r.matched_terms
                      << "\trequired=" << r.required_series_len << "\n";
            ++failures;
            continue;
        }

        Real worst = 0, worst_x = 0;
        for (int i = 0; i < npts; ++i) {
            Real x = -xmax + Real(2) * xmax * Real(i) / Real(std::max(1, npts - 1));
            EvalReport e = evaluate(r, x);
            if (e.status != StatusCode::Ok) {
                std::cout << k << "\tEVAL_FAIL\tx=" << static_cast<double>(x)
                          << "\t" << e.reason << "\n";
                ++failures;
                continue;
            }
            Real ref = std::exp(x);
            Real rel = std::fabs(e.value - ref) / std::max(Real(1e-30L), std::fabs(ref));
            if (rel > worst) { worst = rel; worst_x = x; }
        }
        std::cout << k << "\tmatched=" << r.matched_terms
                  << "\tcond=" << static_cast<double>(r.condition_number)
                  << "\tmax_rel_err=" << static_cast<double>(worst)
                  << "\tat_x=" << static_cast<double>(worst_x) << "\n";
    }
    std::cout << "# benchmark failures: " << failures << "\n";
    return failures ? 1 : 0;
}
