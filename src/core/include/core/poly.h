#pragma once
// Univariate dense polynomials over a ring described by an Ops type.
//
// Coefficients are stored low-degree-first and canonicalised (no trailing
// zero); the zero polynomial is an empty coefficient vector. All algorithms
// below are generic over the digit domain: the same code path performs exact
// integer multipoint evaluation and prime-field multipoint evaluation.
#include <vector>
#include <cstddef>
#include <cstdint>

namespace mp::core {

template <class D>
struct Poly {
  std::vector<D> c;

  Poly() = default;
  explicit Poly(std::vector<D> v) : c(std::move(v)) {}

  bool zero() const { return c.empty(); }
  size_t deg() const { return c.empty() ? 0 : c.size() - 1; }
};

namespace polyops {

template <class Ops>
using Dg = typename Ops::D;

template <class Ops>
bool is_zero(const Ops& ops, const Dg<Ops>& a) {
  if constexpr (Ops::kModular) { (void)ops; return a == 0; }
  else { (void)ops; return a == 0; }
}

template <class Ops>
void canonical(const Ops& ops, std::vector<Dg<Ops>>& v) {
  while (!v.empty() && is_zero(ops, v.back())) v.pop_back();
}

// Schoolbook convolution; coefficients with degree >= cap are discarded.
template <class Ops>
std::vector<Dg<Ops>> conv_school(const Ops& ops,
                                 const std::vector<Dg<Ops>>& a,
                                 const std::vector<Dg<Ops>>& b,
                                 size_t cap) {
  if (a.empty() || b.empty() || cap == 0) return {};
  size_t n = std::min(cap, a.size() + b.size() - 1);
  std::vector<Dg<Ops>> r(n, ops.zero());
  for (size_t i = 0; i < a.size() && i < cap; ++i) {
    if (is_zero(ops, a[i])) continue;
    size_t jmax = std::min(b.size(), cap - i);
    for (size_t j = 0; j < jmax; ++j) {
      Dg<Ops> t;
      ops.mul(t, a[i], b[j]);
      ops.add(r[i + j], r[i + j], t);
    }
  }
  canonical(ops, r);
  return r;
}

namespace detail {
template <class Ops>
std::vector<Dg<Ops>> add_vec(const Ops& ops,
                             const std::vector<Dg<Ops>>& a,
                             const std::vector<Dg<Ops>>& b) {
  std::vector<Dg<Ops>> r(std::max(a.size(), b.size()), ops.zero());
  for (size_t i = 0; i < a.size(); ++i) r[i] = a[i];
  for (size_t i = 0; i < b.size(); ++i) ops.add(r[i], r[i], b[i]);
  return r;
}
template <class Ops>
std::vector<Dg<Ops>> sub_vec(const Ops& ops,
                             const std::vector<Dg<Ops>>& a,
                             const std::vector<Dg<Ops>>& b) {
  std::vector<Dg<Ops>> r(std::max(a.size(), b.size()), ops.zero());
  for (size_t i = 0; i < a.size(); ++i) r[i] = a[i];
  for (size_t i = 0; i < b.size(); ++i) ops.sub(r[i], r[i], b[i]);
  canonical(ops, r);
  return r;
}
} // namespace detail

// Karatsuba convolution truncated to degree < cap. Cross-over threshold 32
// was chosen as the point where schoolbook loses on both target digit types.
template <class Ops>
std::vector<Dg<Ops>> conv_karat(const Ops& ops,
                                const std::vector<Dg<Ops>>& a,
                                const std::vector<Dg<Ops>>& b,
                                size_t cap,
                                size_t threshold = 32) {
  if (a.empty() || b.empty() || cap == 0) return {};
  size_t full = a.size() + b.size() - 1;
  if (cap >= full && (std::min(a.size(), b.size()) <= threshold))
    return conv_school(ops, a, b, cap);

  size_t n = std::max(a.size(), b.size());
  if (n <= threshold) return conv_school(ops, a, b, cap);

  size_t h = n / 2;
  auto split = [&](const std::vector<Dg<Ops>>& v) {
    std::vector<Dg<Ops>> lo(v.begin(), v.begin() + std::min(h, v.size()));
    std::vector<Dg<Ops>> hi(h < v.size() ? v.begin() + h : v.end(), v.end());
    return std::pair{std::move(lo), std::move(hi)};
  };
  auto [a0, a1] = split(a);
  auto [b0, b1] = split(b);
  auto z0 = conv_karat(ops, a0, b0, cap, threshold);
  auto z2 = conv_karat(ops, a1, b1, cap > 2 * h ? cap - 2 * h : 0, threshold);
  auto sa = detail::add_vec(ops, a0, a1);
  auto sb = detail::add_vec(ops, b0, b1);
  auto z1 = conv_karat(ops, sa, sb, cap > h ? cap - h : 0, threshold);
  z1 = detail::sub_vec(ops, z1, z0);
  z1 = detail::sub_vec(ops, z1, z2);

  std::vector<Dg<Ops>> r(std::min(cap, full), ops.zero());
  auto merge = [&](const std::vector<Dg<Ops>>& z, size_t shift) {
    for (size_t i = 0; i < z.size() && i + shift < r.size(); ++i)
      ops.add(r[i + shift], r[i + shift], z[i]);
  };
  merge(z0, 0);
  merge(z1, h);
  merge(z2, 2 * h);
  canonical(ops, r);
  return r;
}

// Multiply two polynomials (full product).
template <class Ops>
std::vector<Dg<Ops>> multiply(const Ops& ops,
                              const std::vector<Dg<Ops>>& a,
                              const std::vector<Dg<Ops>>& b) {
  if (a.empty() || b.empty()) return {};
  return conv_karat(ops, a, b, a.size() + b.size() - 1);
}

// Reciprocal of a power series h with constant term 1, truncated to n terms:
// returns r such that h*r == 1 (mod x^n). Newton doubling:
//   r <- r * (2 - h*r) (mod x^{2k})
template <class Ops>
std::vector<Dg<Ops>> inv_series(const Ops& ops,
                                std::vector<Dg<Ops>> h,
                                size_t n) {
  if (n == 0) return {};
  if (h.size() > n) h.resize(n);
  std::vector<Dg<Ops>> r(1, ops.one());
  size_t k = 1;
  while (k < n) {
    size_t cap = std::min(2 * k, n);
    auto t = conv_karat(ops, h, r, cap);
    t.resize(cap, ops.zero());
    // t <- 2 - t
    Dg<Ops> two;
    ops.add(two, ops.one(), ops.one());
    for (auto& d : t) ops.neg(d, d);
    ops.add(t[0], t[0], two);
    r = conv_karat(ops, r, t, cap);
    r.resize(cap, ops.zero());
    k = cap;
  }
  canonical(ops, r);
  return r;
}

// Polynomial remainder f mod g where g is MONIC. Uses reversed-series
// division: with m=deg f, d=deg g, q^R = f^R * (g^R)^{-1} truncated to
// m-d+1 terms (valid exactly because g's leading coefficient is 1), then
// r = f - q*g truncated to degree d-1.
// Returns both quotient and remainder.
template <class Ops>
void divmod_monic(const Ops& ops,
                  const std::vector<Dg<Ops>>& f,
                  const std::vector<Dg<Ops>>& g,
                  std::vector<Dg<Ops>>& q,
                  std::vector<Dg<Ops>>& r) {
  if (f.size() < g.size()) { q = {}; r = f; return; }
  size_t m = f.size() - 1;
  size_t d = g.size() - 1;
  size_t nq = m - d + 1; // number of quotient coefficients

  auto rev_n = [](const std::vector<Dg<Ops>>& v, size_t n) {
    std::vector<Dg<Ops>> out(n, typename Ops::D{});
    for (size_t i = 0; i < v.size() && i < n; ++i) out[i] = v[v.size() - 1 - i];
    return out;
  };
  auto fr = rev_n(f, f.size());
  auto gr = rev_n(g, g.size());
  auto inv = inv_series(ops, gr, nq);
  auto qr = conv_karat(ops, fr, inv, nq);
  qr.resize(nq, ops.zero());
  q.assign(qr.rbegin(), qr.rend());

  auto qg = conv_karat(ops, q, g, f.size());
  r = f;
  r.resize(g.size() - 1, ops.zero());
  for (size_t i = 0; i < qg.size() && i < g.size() - 1; ++i)
    ops.sub(r[i], r[i], qg[i]);
  canonical(ops, r);
}

template <class Ops>
std::vector<Dg<Ops>> mod_monic(const Ops& ops,
                               const std::vector<Dg<Ops>>& f,
                               const std::vector<Dg<Ops>>& g) {
  std::vector<Dg<Ops>> q, r;
  divmod_monic(ops, f, g, q, r);
  return r;
}

// Pointwise Horner evaluation at one point: used by tests/reference and the
// defensive cross-check, never by the tree algorithm itself.
template <class Ops>
Dg<Ops> horner(const Ops& ops,
               const std::vector<Dg<Ops>>& f,
               const Dg<Ops>& x) {
  if (f.empty()) return ops.zero();
  Dg<Ops> y = f.back();
  for (size_t i = f.size() - 1; i-- > 0;) ops.ax(y, y, x, f[i]);
  return y;
}

} // namespace polyops
} // namespace mp::core
