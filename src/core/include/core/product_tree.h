#pragma once
// Subproduct tree of monic polynomials M_i(x)=x-r_i and its remainder-tree
// multipoint evaluation. Duplicate points are first-class: nothing divides by
// (x-r) more than once, so repeated roots can never trigger a zero-divisor
// (in a field) or a non-monic divisor.
#include <vector>
#include <cstddef>

namespace mp::core {

template <class Ops>
struct ProductTree {
  using V = std::vector<typename Ops::D>;
  std::vector<std::vector<V>> level; // level[0]: linear factors; level.back(): root
  size_t count{0};

  // Build from canonical ring digits r_i (already reduced for a field).
  void build(const Ops& ops, const std::vector<typename Ops::D>& roots);

  // Evaluate f at every root. Output order matches root order exactly; the
  // returned vector has one value per root, duplicates included.
  std::vector<typename Ops::D> evaluate(const Ops& ops, const V& f) const;

  // Linear factor x - r: coefficients low-to-high = [-r, 1].
  static V linear(const Ops& ops, const typename Ops::D& r);
};

// Convenience: build once, evaluate once.
template <class Ops>
std::vector<typename Ops::D> evaluate_batch(
    const Ops& ops,
    const std::vector<typename Ops::D>& roots,
    const std::vector<typename Ops::D>& f) {
  ProductTree<Ops> tree;
  tree.build(ops, roots);
  return tree.evaluate(ops, f);
}

} // namespace mp::core

#include "product_tree.tcc"
