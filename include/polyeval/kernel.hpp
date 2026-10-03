#pragma once
// Product tree + remainder tree driven multi-point polynomial evaluation.
//
// Given polynomial P (degree d) and points x_0..x_{n-1}:
//   1. product tree  : monic M_v = prod_{i in v} (X - x_i)
//   2. root remainder: R_root = P mod M_root
//   3. remainder tree : R_child = R_parent mod M_child  (all M_* monic)
// At a leaf, R is constant and equals P(x_i). All divisors are monic, so no
// modular inverses and no divisions are performed: duplicate points therefore
// never cause a divide-by-zero, and outputs stay in request order.
//
// Multiplication is schoolbook (Eigen-vectorised cwiseProduct over segments);
// remainder against a monic divisor is likewise exact and inverse-free.
#include <cstdint>
#include <vector>

#include "polyeval/eigen_support.hpp"
#include "polyeval/field.hpp"

namespace polyeval::kernel {

struct KernelStats {
  std::uint64_t multiply_scalar_ops = 0;
  std::uint64_t remainder_scalar_ops = 0;
  std::uint64_t node_multiplies = 0;
  std::uint64_t node_remainders = 0;
  void reset() { *this = KernelStats{}; }
};

// Global-ish counters (single-threaded kernel; see README).
KernelStats& stats();

template <class T>
struct Poly {
  // Coefficients ascending: coeff[k] is the coefficient of X^k.
  std::vector<T> coeff;

  Poly() = default;
  explicit Poly(std::size_t degree_plus_one) : coeff(degree_plus_one, T{}) {}
  std::size_t deg() const { return coeff.empty() ? 0 : coeff.size() - 1; }
  void trim() {
    while (coeff.size() > 1 && coeff.back() == T{}) coeff.pop_back();
  }
};

template <class T>
Poly<T> multiply(const Poly<T>& a, const Poly<T>& b);

// r = a mod b where b is monic. Inverse-free subtractive elimination.
template <class T>
Poly<T> rem_monic(const Poly<T>& a, const Poly<T>& b);

template <class T>
struct ProductTree {
  using P = Poly<T>;
  // levels_[0] = leaves; levels_.back() = {root}.
  std::vector<std::vector<P>> levels_;
  std::size_t npoints_ = 0;

  void build(const std::vector<T>& points);
  const P& root() const { return levels_.back().front(); }
};

// Evaluate P at every point using the tree. Result order matches points.
template <class T>
std::vector<T> eval_batch(const Poly<T>& p, const std::vector<T>& points);

// Number of scalar coefficient slots occupied by a product tree over n leaves.
// Slot counts are mode independent; the caller multiplies by a per-scalar
// byte estimate to enforce a memory budget.
std::uint64_t product_tree_slots(std::size_t n) noexcept;

// Choose the largest batch size k <= n such that tree slots fit the budget.
// Returns 0 when even a single-point tree cannot fit (min_slots(batch=1)=2).
std::size_t choose_batch_size(std::size_t n, std::uint64_t byte_budget,
                              std::uint64_t bytes_per_scalar) noexcept;

// ---- Explicit instantiations live in kernel.cpp ----
extern template struct Poly<field::ModInt>;
extern template struct Poly<boost::multiprecision::cpp_int>;
extern template struct ProductTree<field::ModInt>;
extern template struct ProductTree<boost::multiprecision::cpp_int>;
extern template Poly<field::ModInt> multiply(const Poly<field::ModInt>&, const Poly<field::ModInt>&);
extern template Poly<boost::multiprecision::cpp_int> multiply(
    const Poly<boost::multiprecision::cpp_int>&, const Poly<boost::multiprecision::cpp_int>&);
extern template Poly<field::ModInt> rem_monic(const Poly<field::ModInt>&, const Poly<field::ModInt>&);
extern template Poly<boost::multiprecision::cpp_int> rem_monic(
    const Poly<boost::multiprecision::cpp_int>&, const Poly<boost::multiprecision::cpp_int>&);
extern template std::vector<field::ModInt> eval_batch(const Poly<field::ModInt>&,
                                                      const std::vector<field::ModInt>&);
extern template std::vector<boost::multiprecision::cpp_int> eval_batch(
    const Poly<boost::multiprecision::cpp_int>&,
    const std::vector<boost::multiprecision::cpp_int>&);

}  // namespace polyeval::kernel
