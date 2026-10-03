#pragma once
#include "polyeval/kernel.hpp"

#include <algorithm>
#include <cstddef>

namespace polyeval::kernel {

template <class T>
Poly<T> multiply(const Poly<T>& a, const Poly<T>& b) {
  if (a.coeff.empty() || b.coeff.empty()) return Poly<T>{};
  const std::size_t na = a.coeff.size();
  const std::size_t nb = b.coeff.size();
  Poly<T> r(na + nb - 1);

  // Eigen segment dot-products accumulate one output coefficient at a time.
  const std::size_t maxk = na + nb - 2;
  for (std::size_t k = 0; k <= maxk; ++k) {
    const std::size_t i0 = (k + 1 > nb) ? (k + 1 - nb) : 0;
    const std::size_t i1 = std::min(na, k + 1);  // exclusive
    const std::size_t len = i1 - i0;
    if (len == 0) continue;
    // r_k = sum_t a[i0+t] * b[k-(i0+t)], both read ascending => reverse b seg.
    using namespace Eigen;
    Map<const Matrix<T, Dynamic, 1>> av(a.coeff.data() + i0, static_cast<Index>(len));
    Matrix<T, Dynamic, 1> bseg(len);
    for (std::size_t t = 0; t < len; ++t) bseg[static_cast<Index>(t)] = b.coeff[k - i0 - t];
    r.coeff[k] = av.cwiseProduct(bseg).sum();
  }
  stats().multiply_scalar_ops += static_cast<std::uint64_t>(na) *
                                 static_cast<std::uint64_t>(nb);
  ++stats().node_multiplies;
  r.trim();
  return r;
}

template <class T>
Poly<T> rem_monic(const Poly<T>& a, const Poly<T>& b) {
  // b must be monic. Degree-d steps eliminate each leading term by a pure
  // subtraction: coefficient at index (j + i) -= lead * b_i, with b_m = 1.
  Poly<T> r = a;
  const std::size_t m = b.deg();
  if (r.deg() < m) return r;
  const auto& bc = b.coeff;
  for (std::size_t k = r.coeff.size() - 1; k >= m; --k) {
    T lead = r.coeff[k];
    if (lead == T{}) continue;
    for (std::size_t i = 0; i < m; ++i) {
      if (bc[i] == T{}) continue;
      r.coeff[k - m + i] = r.coeff[k - m + i] - lead * bc[i];
    }
    r.coeff[k] = T{};
    stats().remainder_scalar_ops += m;
    if (k == m) break;
  }
  r.trim();
  ++stats().node_remainders;
  return r;
}

template <class T>
void ProductTree<T>::build(const std::vector<T>& points) {
  npoints_ = points.size();
  levels_.clear();
  if (points.empty()) return;
  levels_.reserve(64);  // prevents reference invalidation during emplace_back
  auto& leaves = levels_.emplace_back();
  leaves.reserve(points.size());
  for (const T& x : points) {
    P leaf(2);
    leaf.coeff[0] = T{} - x;  // -x
    leaf.coeff[1] = T{} + T(1);  // 1
    leaves.push_back(std::move(leaf));
  }
  while (levels_.back().size() > 1) {
    const std::vector<P>& prev = levels_.back();
    const std::size_t prev_size = prev.size();
    auto& cur = levels_.emplace_back();
    cur.reserve((prev_size + 1) / 2);
    for (std::size_t i = 0; i < prev_size; i += 2) {
      if (i + 1 < prev_size) {
        cur.push_back(multiply(prev[i], prev[i + 1]));
      } else {
        cur.push_back(prev[i]);  // odd node lifted (copied; prev stays valid)
      }
    }
  }
}

namespace detail {
// Iterative remainder-tree traversal carrying (level, node-index, polynomial).
template <class T>
void descend_leaves(const ProductTree<T>& tree, Poly<T> current,
                    std::vector<T>& out) {
  struct Frame {
    std::size_t level;
    std::size_t index;
    Poly<T> poly;
  };
  std::vector<Frame> stack;
  stack.push_back({tree.levels_.size() - 1, 0, std::move(current)});
  while (!stack.empty()) {
    Frame f = std::move(stack.back());
    stack.pop_back();
    if (f.level == 0) {
      out[f.index] = f.poly.coeff.empty() ? T{} : f.poly.coeff.front();
      continue;
    }
    const auto& child_level = tree.levels_[f.level - 1];
    const std::size_t left = 2 * f.index;
    const std::size_t right = left + 1;
    // Push right first so left is processed first (order irrelevant to result,
    // keeps traversal easy to follow in logs).
    if (right < child_level.size()) {
      stack.push_back({f.level - 1, right, rem_monic(f.poly, child_level[right])});
    }
    stack.push_back({f.level - 1, left, rem_monic(f.poly, child_level[left])});
  }
}
}  // namespace detail

template <class T>
std::vector<T> eval_batch(const Poly<T>& p, const std::vector<T>& points) {
  std::vector<T> out(points.size());
  if (points.empty()) return out;
  ProductTree<T> tree;
  tree.build(points);
  Poly<T> root_rem = rem_monic(p, tree.root());
  detail::descend_leaves(tree, std::move(root_rem), out);
  return out;
}

}  // namespace polyeval::kernel
