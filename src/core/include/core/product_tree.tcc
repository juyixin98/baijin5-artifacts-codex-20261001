#pragma once
#include "core/product_tree.h"
#include "core/poly.h"
#include <algorithm>

namespace mp::core {

template <class Ops>
typename ProductTree<Ops>::V ProductTree<Ops>::linear(
    const Ops& ops, const typename Ops::D& r) {
  V v(2, ops.zero());
  ops.neg(v[0], r); // -r
  v[1] = ops.one(); // monic
  return v;
}

template <class Ops>
void ProductTree<Ops>::build(const Ops& ops,
                             const std::vector<typename Ops::D>& roots) {
  count = roots.size();
  level.clear();
  if (count == 0) return;
  auto& leaves = level.emplace_back();
  leaves.reserve(count);
  for (const auto& r : roots) leaves.push_back(linear(ops, r));

  while (level.back().size() > 1) {
    const auto& cur = level.back();
    std::vector<V> nxt;
    nxt.reserve((cur.size() + 1) / 2);
    for (size_t i = 0; i < cur.size(); i += 2) {
      if (i + 1 < cur.size())
        nxt.push_back(polyops::multiply(ops, cur[i], cur[i + 1]));
      else
        nxt.push_back(cur[i]); // odd node promoted unchanged
    }
    level.push_back(std::move(nxt));
  }
}

template <class Ops>
std::vector<typename Ops::D> ProductTree<Ops>::evaluate(
    const Ops& ops, const V& f) const {
  std::vector<typename Ops::D> values;
  values.reserve(count);
  if (count == 0) return values;

  // Descend: at the root reduce f by the root product, then each node
  // reduces its parent's remainder by its own node polynomial.
  V root_rem = polyops::mod_monic(ops, f, level.back().front());
  std::vector<const V*> cur_rems(1, nullptr);
  std::vector<V> cur_vals(1);
  cur_vals[0] = std::move(root_rem);

  for (size_t li = level.size(); li-- > 0;) {
    const auto& nodes = level[li];
    std::vector<V> next_vals;
    next_vals.reserve(nodes.size());
    for (size_t i = 0; i < nodes.size(); ++i) {
      const V& parent = cur_vals[i / 2];
      next_vals.push_back(polyops::mod_monic(ops, parent, nodes[i]));
    }
    cur_vals = std::move(next_vals);
    if (li == 0) break;
  }

  // Leaf remainder of f mod (x-r) is the constant f(r). Return constants in
  // requested order, including every duplicate point.
  for (const auto& rem : cur_vals)
    values.push_back(rem.empty() ? ops.zero() : rem[0]);
  return values;
}
} // namespace mp::core
