// Independent reference kernel for cross-checking the core implementation.
//
// Independence properties (nothing here calls into src/cheb):
//   * long double arithmetic (extended precision where the platform offers
//     it); verified at runtime via precision_guard().
//   * coefficients from a DIRECT cosine sum with Kahan compensation
//     (not the core's VectorXd DCT routine).
//   * evaluation builds the Chebyshev basis T_k explicitly via the three-term
//     recurrence (not the core's Clenshaw recurrence), Kahan compensated.
#ifndef CHEB_REFERENCE_HPP
#define CHEB_REFERENCE_HPP

#include <algorithm>
#include <cassert>
#include <cmath>
#include <functional>
#include <limits>
#include <vector>

namespace chebref {

using FuncLD = std::function<long double(long double)>;

inline long double pi_ld() { return std::acos(-1.0L); }

inline bool precision_guard() {
  return std::numeric_limits<long double>::digits >=
         std::numeric_limits<double>::digits + 10;
}

inline long double map_to_ref(long double lo, long double hi, long double x) {
  return (2.0L * x - (hi + lo)) / (hi - lo);
}
inline long double map_from_ref(long double lo, long double hi,
                                long double t) {
  return 0.5L * (lo + hi) + 0.5L * (hi - lo) * t;
}

inline std::vector<long double> fit(const FuncLD& f, long double lo,
                                    long double hi, int n) {
  assert(n >= 1);
  std::vector<long double> fj(n + 1);
  for (int j = 0; j <= n; ++j) {
    const long double t = std::cos(pi_ld() * j / n);
    fj[j] = f(map_from_ref(lo, hi, t));
  }
  std::vector<long double> a(n + 1);
  for (int k = 0; k <= n; ++k) {
    long double s = 0.0L, comp = 0.0L;
    auto add = [&](long double v) {
      const long double y = v - comp;
      const long double z = s + y;
      comp = (z - s) - y;
      s = z;
    };
    add(0.5L * fj[0]);
    add(0.5L * (k % 2 ? -1.0L : 1.0L) * fj[n]);
    for (int j = 1; j < n; ++j)
      add(fj[j] * std::cos(pi_ld() * j * k / n));
    s *= 2.0L / n;
    if (k == 0 || k == n) s *= 0.5L;
    a[k] = s;
  }
  return a;
}

// Explicit-basis evaluation: p = a0*T0 + sum a_k T_k via the three-term basis
// recurrence (a different algorithm from the core's Clenshaw).
inline long double evaluate(const std::vector<long double>& a, long double lo,
                            long double hi, long double x) {
  const long double t = map_to_ref(lo, hi, x);
  const int n = static_cast<int>(a.size()) - 1;
  long double sum = 0.0L, comp = 0.0L;
  auto add = [&](long double v) {
    const long double y = v - comp;
    const long double z = sum + y;
    comp = (z - sum) - y;
    sum = z;
  };
  long double tkm1 = 1.0L;
  long double tk = t;
  add(a[0] * tkm1);
  for (int k = 1; k <= n; ++k) {
    if (k == 1) {
      add(a[k] * tk);
    } else {
      const long double tkp1 = 2.0L * t * tk - tkm1;
      tkm1 = tk;
      tk = tkp1;
      add(a[k] * tk);
    }
  }
  return sum;
}

inline long double max_residual(const std::vector<long double>& a,
                                long double lo, long double hi,
                                const FuncLD& f, int points) {
  long double worst = 0.0L;
  for (int i = 0; i <= points; ++i) {
    const long double x =
        lo + (hi - lo) * static_cast<long double>(i) / points;
    worst = std::max(worst, std::fabs(evaluate(a, lo, hi, x) - f(x)));
  }
  return worst;
}

}  // namespace chebref

#endif
