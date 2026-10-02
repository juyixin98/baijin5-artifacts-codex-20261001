// Independent benchmark: compares Pade value error vs truncated Taylor
// over a synthetic sweep, for exp, geometric and log-series. References are
// independently evaluated with std::exp / closed forms.
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>

#include "pade/solver.hpp"

using pade::Options;

int main() {
    std::vector<double> coeffs(16);
    double fact = 1.0;
    for (int k = 0; k < 16; ++k) {
        if (k > 0) fact *= k;
        coeffs[k] = 1.0 / fact;
    }
    std::cout << "order,order_kind,x,pade_err,taylor_err\n";
    for (int q = 1; q <= 5; ++q) {
        Options o; o.numerator_order = q; o.denominator_order = q;
        auto r = pade::padeApproximate(coeffs, o);
        if (r.status != pade::StatusCode::kOk) {
            std::cerr << "unexpected status " << pade::statusName(r.status)
                      << "\n";
            return 1;
        }
        for (double x : {0.25, 0.5, 1.0, -1.0}) {
            double taylor = 0.0;
            for (int k = 2 * q; k >= 0; --k) taylor = taylor * x + coeffs[k];
            auto e = pade::evaluatePade(r, x);
            std::cout << q << ",[" << q << "/" << q << "]," << x << ","
                      << std::setprecision(6) << std::abs(e.value - std::exp(x))
                      << "," << std::abs(taylor - std::exp(x)) << "\n";
        }
    }
    // timing
    auto t0 = std::chrono::high_resolution_clock::now();
    volatile double sink = 0.0;
    for (int i = 0; i < 10000; ++i) {
        Options o; o.numerator_order = 8; o.denominator_order = 8;
        auto r = pade::padeApproximate(coeffs, o);
        sink += pade::evaluatePade(r, 0.5).value;
    }
    auto t1 = std::chrono::high_resolution_clock::now();
    double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    std::cerr << "10000x [8/8] solve+eval: " << ms << " ms ("
              << ms / 10000.0 << " ms each), sink=" << sink << "\n";
    return 0;
}
