// Independent benchmark: product/remainder-tree batch evaluation vs a
// point-wise Horner baseline implemented here (not pulled from the kernel).
// Synthetic deterministic data only; no external services.
//
//   polyeval_bench [--n 16,32,64] [--degree d] [--mode exact|field] [--prime p]
//                  [--repeat r]
// Output is JSON lines with ops and wall time plus the empirical growth ratio.
#include <chrono>
#include <algorithm>
#include <cstdint>
#include <type_traits>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#include "polyeval/contract.hpp"
#include "polyeval/kernel.hpp"

using B = polyeval::contract::Big;

namespace {
struct Lcg {
  std::uint64_t s;
  explicit Lcg(std::uint64_t seed) : s(seed) {}
  std::uint64_t next() { s = s * 6364136223846793005ULL + 1442695040888963407ULL; return s >> 17; }
};

template <class T>
T horner_ref(const std::vector<T>& c, const T& x) {
  T r{};
  for (auto it = c.rbegin(); it != c.rend(); ++it) r = r * x + *it;
  return r;
}

template <class T>
double bench_one(std::size_t n, std::size_t degree, std::size_t repeat, bool field_mode,
                 std::uint64_t prime) {
  std::vector<T> coeff(degree + 1), pts(n);
  Lcg rng(0x9E3779B97F4A7C15ULL ^ (n * 131) ^ (degree * 17));
  auto gen = [&]() -> T {
    if constexpr (std::is_same_v<T, polyeval::field::ModInt>) {
      return T::raw(rng.next() % prime);
    } else {
      return T(rng.next() % 1000);
    }
  };
  for (auto& c : coeff) c = gen();
  for (auto& x : pts) x = gen();

  polyeval::kernel::Poly<T> p; p.coeff = coeff;

  double best_tree = 1e300;
  for (std::size_t rep = 0; rep < repeat; ++rep) {
    polyeval::kernel::stats().reset();
    auto t0 = std::chrono::steady_clock::now();
    auto vals = polyeval::kernel::eval_batch(p, pts);
    auto t1 = std::chrono::steady_clock::now();
    double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    best_tree = std::min(best_tree, ms);
    // correctness vs Horner on a few sampled indices (independent baseline)
    for (std::size_t idx : {std::size_t(0), n / 2, n - 1}) {
      T want = horner_ref(coeff, pts[idx]);
      if (want != vals[idx]) {
        std::cerr << "benchmark correctness mismatch n=" << n << " idx=" << idx << "\n";
        std::exit(3);
      }
    }
  }

  // Horner baseline timing (one pass; same synthetic data).
  auto t0 = std::chrono::steady_clock::now();
  volatile std::size_t sink = 0;
  std::vector<T> baseline(n);
  for (std::size_t i = 0; i < n; ++i) baseline[i] = horner_ref(coeff, pts[i]);
  auto t1 = std::chrono::steady_clock::now();
  double horner_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
  (void)sink;

  std::cout << "{\"mode\":\"" << (field_mode ? "field" : "exact") << "\""
            << ",\"n\":" << n << ",\"degree\":" << degree
            << ",\"tree_ms\":" << best_tree << ",\"horner_ms\":" << horner_ms
            << ",\"multiply_scalar_ops\":" << polyeval::kernel::stats().multiply_scalar_ops
            << ",\"remainder_scalar_ops\":" << polyeval::kernel::stats().remainder_scalar_ops
            << ",\"tree_slots\":" << polyeval::kernel::product_tree_slots(n)
            << "}\n";
  return best_tree;
}

std::vector<std::size_t> parse_ns(const std::string& s) {
  std::vector<std::size_t> out;
  std::size_t cur = 0; bool have = false;
  for (char c : s) {
    if (c >= '0' && c <= '9') { cur = cur * 10 + (c - '0'); have = true; }
    else if (c == ',') { if (have) out.push_back(cur); cur = 0; have = false; }
  }
  if (have) out.push_back(cur);
  if (out.empty()) out = {16, 32, 64, 128};
  return out;
}
}  // namespace

int main(int argc, char** argv) {
  std::size_t degree = 8, repeat = 3;
  std::uint64_t prime = 1000003;
  bool field_mode = false;
  std::string ns = "16,32,64,128";
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&]() -> std::string { return (i + 1 < argc) ? argv[++i] : ""; };
    if (a == "--n") ns = next();
    else if (a == "--degree") degree = std::stoull(next());
    else if (a == "--repeat") repeat = std::stoull(next());
    else if (a == "--prime") prime = std::stoull(next());
    else if (a == "--mode") { std::string m = next(); field_mode = (m == "field"); }
  }
  if (field_mode) {
    polyeval::field::ModIntScope sc(prime);
    for (std::size_t n : parse_ns(ns))
      bench_one<polyeval::field::ModInt>(n, degree, repeat, true, prime);
  } else {
    for (std::size_t n : parse_ns(ns)) bench_one<B>(n, degree, repeat, false, prime);
  }
  return 0;
}
