#include "pade/polynomial.hpp"
#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>

namespace pade::poly {
namespace {
Real infnorm(const Vector& a) {
    Real m = 0;
    for (int i = 0; i < a.size(); ++i) m = std::max(m, std::fabs(a[i]));
    return m;
}
}

Real eval(const Vector& a, Real x) noexcept {
    Real y = 0;
    for (int i = a.size() - 1; i >= 0; --i) y = y * x + a[i];
    return y;
}

Vector trim(const Vector& a, Real tol) noexcept {
    if (a.size() <= 1) return a;
    const Real m = infnorm(a);
    int last = static_cast<int>(a.size()) - 1;
    // Drop only top-degree coefficients that are zero; interior zeros (e.g.
    // [0, 0.5] = 0.5 x) must be preserved or the degree collapses wrongly.
    while (last > 0 && std::fabs(a[last]) <= tol * (Real(1) + m)) --last;
    Vector out(last + 1);
    for (int i = 0; i <= last; ++i)
        out[i] = (std::fabs(a[i]) <= tol * (Real(1) + m)) ? Real(0) : a[i];
    return out;
}

Vector makeMonic(const Vector& a) noexcept {
    Vector out = a;
    const Real lead = out[out.size() - 1];
    if (lead != Real(0)) out /= lead;
    return out;
}

DivResult divide(const Vector& a, const Vector& b, Real tol) {
    const int da = static_cast<int>(a.size()) - 1;
    Vector bb = trim(b, tol);
    const int db = static_cast<int>(bb.size()) - 1;
    if (db < 0 || bb[db] == Real(0)) throw std::invalid_argument("division by zero polynomial");
    Vector q(std::max(0, da - db + 1)); q.setZero();
    Vector r = a;                       // fixed length; keep interior zeros
    const Real lead = bb[db];
    int dr = da;
    while (dr >= db) {
        const int shift = dr - db;
        const Real coef = r[dr] / lead;
        q[shift] += coef;
        for (int j = 0; j <= db; ++j) r[shift + j] -= coef * bb[j];
        r[dr] = 0;                      // exact cancellation of the pivot
        --dr;
        while (dr >= 0 && std::fabs(r[dr]) <= tol * (Real(1) + infnorm(a))) {
            r[dr] = 0;
            --dr;
        }
    }
    return {trim(q, tol), trim(r, tol)};
}

Real relativeInfNorm(const Vector& a, const Vector& b) {
    const int n = static_cast<int>(std::max(a.size(), b.size()));
    Real diff = 0, scale = 1;
    for (int i = 0; i < n; ++i) {
        const Real av = i < a.size() ? a[i] : Real(0);
        const Real bv = i < b.size() ? b[i] : Real(0);
        diff = std::max(diff, std::fabs(av - bv));
        scale = std::max(scale, std::fabs(bv));
    }
    return diff / scale;
}

bool divides(const Vector& a, const Vector& b, Real tol, Real* rel_rem) {
    DivResult d = divide(a, b, tol);
    const Real rr = relativeInfNorm(d.remainder, Vector::Zero(1));
    const Real rel = infnorm(d.remainder) / std::max(Real(1), infnorm(a));
    if (rel_rem) *rel_rem = rel;
    (void)rr;
    return rel <= tol;
}

namespace {
// Zero test that survives trim(): compare against the scale of the *input*.
}

Vector gcdMonic(Vector a, Vector b, Real tol, int& iterations) {
    iterations = 0;
    Vector aa = trim(a, tol);
    Vector bb = trim(b, tol);
    const Real base_scale = std::max(Real(1), std::max(infnorm(a), infnorm(b)));
    auto zero = [&](const Vector& v) {
        return infnorm(v) <= tol * base_scale;
    };
    // Euclid: gcd(a,b) = gcd(b, a mod b), monic normalization each turn.
    while (!zero(bb)) {
        ++iterations;
        if (iterations > 256) {
            std::ostringstream os;
            os << "gcd did not converge after " << iterations << " Euclidean steps";
            throw std::runtime_error(os.str());
        }
        DivResult d = divide(aa, bb, tol);
        aa = bb;
        bb = trim(d.remainder, tol);
    }
    return makeMonic(trim(aa, tol));
}

} // namespace pade::poly
