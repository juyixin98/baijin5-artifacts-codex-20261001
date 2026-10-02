#include "pade/polynomial.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

#include <Eigen/Dense>

namespace pade::poly {

std::vector<Real> trim(const std::vector<Real>& p, Real rel_tol) {
    Real mx = 0.0;
    for (Real v : p) mx = std::max(mx, std::abs(v));
    Real cutoff = rel_tol * mx;
    std::size_t last = p.size();
    while (last > 1 && std::abs(p[last - 1]) <= cutoff) --last;
    return std::vector<Real>(p.begin(), p.begin() + last);
}

std::vector<Real> multiply(const std::vector<Real>& a,
                           const std::vector<Real>& b) {
    std::vector<Real> c(a.size() + b.size() - 1, Real(0));
    for (std::size_t i = 0; i < a.size(); ++i)
        for (std::size_t j = 0; j < b.size(); ++j)
            c[i + j] += a[i] * b[j];
    return c;
}

Real evaluate(const std::vector<Real>& p, Real x) {
    Real y = 0.0;
    for (auto it = p.rbegin(); it != p.rend(); ++it) y = y * x + *it;
    return y;
}

int leadingZeroShift(const std::vector<Real>& p, Real abs_tol) {
    int s = 0;
    for (Real v : p) {
        if (std::abs(v) > abs_tol) break;
        ++s;
    }
    return s;
}

std::vector<Real> divideByMonic(const std::vector<Real>& a,
                                const std::vector<Real>& d) {
    // d must be monic: d.back() == 1.
    std::vector<Real> r = a;
    int ddeg = static_cast<int>(d.size()) - 1;
    int adeg = static_cast<int>(r.size()) - 1;
    if (adeg < ddeg) return {Real(0)};
    std::vector<Real> q(adeg - ddeg + 1, Real(0));
    for (int k = adeg - ddeg; k >= 0; --k) {
        q[k] = r[k + ddeg];
        for (int j = 0; j < ddeg; ++j) r[k + j] -= q[k] * d[j];
    }
    return q;
}

ApproxGcd approximateGcd(const std::vector<Real>& aIn,
                         const std::vector<Real>& bIn,
                         Real rel_tol) {
    // Euclidean algorithm with relative pivot tolerance. Inputs are first
    // stripped of exact monomial factors (that is handled separately and
    // provably), then trimmed.
    auto strip = [&](std::vector<Real> p) {
        p = trim(p, rel_tol);
        int s = leadingZeroShift(p, rel_tol * Real(2));
        return std::vector<Real>(p.begin() + s, p.end());
    };
    std::vector<Real> a = strip(aIn);
    std::vector<Real> b = strip(bIn);
    if (a.size() < b.size()) std::swap(a, b);

    Real scale = 0.0;
    for (Real v : a) scale = std::max(scale, std::abs(v));
    for (Real v : b) scale = std::max(scale, std::abs(v));
    Real abs_tol = rel_tol * scale;
    if (scale == Real(0)) return {{Real(1)}, 0};

    auto monic = [](std::vector<Real> p) {
        Real lc = p.back();
        if (lc == Real(0)) return p;
        for (Real& v : p) v /= lc;
        return p;
    };

    std::vector<Real> g;
    while (b.size() > 1 || (b.size() == 1 && std::abs(b[0]) > abs_tol)) {
        // Long division: a = q*b + r (b monic-normalized conceptually).
        std::vector<Real> bn = monic(b);
        int ddeg = static_cast<int>(bn.size()) - 1;
        std::vector<Real> r = a;
        for (int k = static_cast<int>(r.size()) - 1; k >= ddeg; --k) {
            Real qk = r[k] / bn[ddeg];
            for (int j = 0; j <= ddeg; ++j) r[k - ddeg + j] -= qk * bn[j];
        }
        r.resize(std::max(1, ddeg));
        r = trim(r, rel_tol);
        g = bn;
        a = bn;
        b = r;
        if (b.size() == 1 && std::abs(b[0]) <= abs_tol) break;
    }
    if (g.empty()) return {{Real(1)}, 0};
    g = monic(trim(g, rel_tol));
    int deg = static_cast<int>(g.size()) - 1;
    if (deg <= 0) return {{Real(1)}, 0};
    return {g, deg};
}

std::vector<std::complex<Real>> roots(const std::vector<Real>& p) {
    int deg = static_cast<int>(p.size()) - 1;
    while (deg > 0 && p[deg] == Real(0)) --deg;
    if (deg <= 0) return {};
    Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic> c =
        Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic>::Zero(deg, deg);
    for (int i = 0; i + 1 < deg; ++i) c(i, i + 1) = Real(1);
    Real lc = p[deg];
    for (int i = 0; i < deg; ++i) c(deg - 1, i) = -p[i] / lc;
    Eigen::ComplexEigenSolver<Eigen::Matrix<Real, Eigen::Dynamic, Eigen::Dynamic>> es(c, false);
    std::vector<std::complex<Real>> out;
    auto ev = es.eigenvalues();
    for (int i = 0; i < ev.size(); ++i) out.push_back(ev[i]);
    return out;
}

} // namespace pade::poly
