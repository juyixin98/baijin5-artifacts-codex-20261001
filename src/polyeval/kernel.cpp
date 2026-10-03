#include "kernel_impl.hpp"

namespace polyeval::kernel {
using F = field::ModInt;
using B = boost::multiprecision::cpp_int;

// Definitions for non-template translation-unit functions (the inline
// versions in kernel_impl.hpp provide the bodies; emit one external copy).
KernelStats& stats() {
  static KernelStats s;
  return s;
}
std::uint64_t product_tree_slots(std::size_t n) noexcept {
  // Each node over span s stores a monic polynomial of degree s: s+1 slots.
  // Total = (sum of all node spans) + (number of nodes). Sum of node spans
  // equals n per level except the leaf level (n leaves of span 1 => n too),
  // i.e. n * L, where L is the number of levels.
  if (n == 0) return 0;
  std::uint64_t levels = 1;
  for (std::size_t x = n; x > 1; x = (x + 1) / 2) ++levels;
  std::uint64_t nodes = 0;
  for (std::size_t x = n; ; x = (x + 1) / 2) {
    nodes += x;
    if (x <= 1) break;
  }
  return static_cast<std::uint64_t>(n) * levels + nodes;
}
std::size_t choose_batch_size(std::size_t n, std::uint64_t byte_budget,
                              std::uint64_t bytes_per_scalar) noexcept {
  if (n == 0 || bytes_per_scalar == 0) return 0;
  if (product_tree_slots(1) * bytes_per_scalar > byte_budget) return 0;
  std::size_t lo = 1, hi = n;
  while (lo < hi) {
    std::size_t mid = lo + (hi - lo + 1) / 2;
    if (product_tree_slots(mid) * bytes_per_scalar <= byte_budget) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

template struct Poly<F>;
template struct Poly<B>;
template struct ProductTree<F>;
template struct ProductTree<B>;

template Poly<F> multiply(const Poly<F>&, const Poly<F>&);
template Poly<B> multiply(const Poly<B>&, const Poly<B>&);
template Poly<F> rem_monic(const Poly<F>&, const Poly<F>&);
template Poly<B> rem_monic(const Poly<B>&, const Poly<B>&);
template std::vector<F> eval_batch(const Poly<F>&, const std::vector<F>&);
template std::vector<B> eval_batch(const Poly<B>&, const std::vector<B>&);
}  // namespace polyeval::kernel
