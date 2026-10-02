// Kernel polynomial arithmetic over both rings: multiplication (schoolbook and
// Karatsuba paths agree), monic division quotient/remainder identity, and the
// independent Horner comparison.
#include "harness/test.h"
#include "harness/reference.h"
#include "core/poly.h"
#include "core/rings.h"
#include <cstdint>
#include <random>
#include <vector>

using namespace mp::core;
using mp::core::cpp_int;

namespace {
std::vector<cpp_int> iv(std::initializer_list<int64_t> xs) {
  std::vector<cpp_int> v;
  for (int64_t x : xs) v.emplace_back(x);
  return v;
}
std::vector<cpp_int> mul_school(const std::vector<cpp_int>& a,
                                const std::vector<cpp_int>& b) {
  IntegerOps ops;
  return polyops::conv_school(ops, a, b, a.size() + b.size() - 1);
}
std::vector<cpp_int> mul_karat(const std::vector<cpp_int>& a,
                               const std::vector<cpp_int>& b) {
  IntegerOps ops;
  return polyops::conv_karat(ops, a, b, a.size() + b.size() - 1, 4);
}
} // namespace

MP_TEST(karatsuba_matches_schoolbook_on_random_vectors) {
  IntegerOps ops;
  std::mt19937_64 rng(12345);
  for (int trial = 0; trial < 40; ++trial) {
    size_t n = 1 + rng() % 120, m = 1 + rng() % 120;
    std::vector<cpp_int> a(n), b(m);
    for (auto& x : a) x = static_cast<int64_t>(rng() % 2001) - 1000;
    for (auto& x : b) x = static_cast<int64_t>(rng() % 2001) - 1000;
    auto s = mul_school(a, b);
    auto k = mul_karat(a, b);
    MP_CHECK(s == k);
  }
  (void)ops;
}

MP_TEST(monic_division_repairs_f_as_qg_plus_r) {
  IntegerOps ops;
  // g = x^2 - 3x + 2 (monic); f = (x^2-3x+2)*(2x+5) + (x+1)
  auto g = iv({2, -3, 1});
  auto f = iv({});
  {
    auto qg = mul_school(g, iv({5, 2}));
    f = qg;
    while (f.size() < 1) f.push_back(0);
    f.resize(std::max(f.size(), size_t(2)), cpp_int(0));
    f[0] += 1; f[1] += 1; // remainder x+1
  }
  std::vector<cpp_int> q, r;
  polyops::divmod_monic(ops, f, g, q, r);
  MP_CHECK(q == iv({5, 2}));
  MP_CHECK(r == iv({1, 1}));
  auto rebuilt = polyops::multiply(ops, q, g);
  rebuilt.resize(std::max(rebuilt.size(), r.size()), cpp_int(0));
  for (size_t i = 0; i < r.size(); ++i) rebuilt[i] += r[i];
  while (!rebuilt.empty() && rebuilt.back() == 0) rebuilt.pop_back();
  MP_CHECK(rebuilt == f);
}

MP_TEST(field_remainder_division_is_exact_mod_p) {
  FieldOps ops(101);
  // g = x^2 + 1 over F_101 (coeffs: 1,0,1)
  std::vector<uint64_t> g = {1, 0, 1};
  // f = x^3 + 2x + 3 (3,2,0,1)
  std::vector<uint64_t> f = {3, 2, 0, 1};
  std::vector<uint64_t> q, r;
  polyops::divmod_monic(ops, f, g, q, r);
  // Reconstruct q*g + r mod 101 and compare with f.
  auto qg = polyops::multiply(ops, q, g);
  qg.resize(std::max(qg.size(), r.size()), 0);
  for (size_t i = 0; i < r.size(); ++i) {
    qg[i] += r[i];
    if (qg[i] >= 101) qg[i] -= 101;
  }
  MP_CHECK(qg == f);
  // Remainder degree < 2, constant term is f evaluated at a root relationship:
  // check degree bound explicitly.
  MP_CHECK(r.size() <= 2);
}

MP_TEST(horner_constant_and_zero_polynomial_edge_cases) {
  IntegerOps ops;
  MP_CHECK(polyops::horner(ops, iv({}), cpp_int(9)) == 0);
  MP_CHECK(polyops::horner(ops, iv({7}), cpp_int(9)) == 7);
  // f = x (low-first {0,1})
  MP_CHECK(polyops::horner(ops, iv({0, 1}), cpp_int(-4)) == -4);
}
