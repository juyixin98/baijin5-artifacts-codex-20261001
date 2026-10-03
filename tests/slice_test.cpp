// Minimal vertical slice: tree evaluation vs hand-computed Horner values.
#include <cassert>
#include <iostream>
#include <vector>

#include "polyeval/kernel.hpp"

using namespace polyeval;

int main() {
  // P(X) = 1 + 2X + 3X^2
  kernel::Poly<boost::multiprecision::cpp_int> p;
  using B = boost::multiprecision::cpp_int;
  p.coeff = {B(1), B(2), B(3)};
  std::vector<B> pts = {B(0), B(1), B(2), B(3), B(-2), B(2)};  // includes duplicate 2

  auto horner = [&](B x) {
    B r(0);
    for (auto it = p.coeff.rbegin(); it != p.coeff.rend(); ++it) r = r * x + *it;
    return r;
  };
  auto got = kernel::eval_batch(p, pts);
  for (std::size_t i = 0; i < pts.size(); ++i) {
    B want = horner(pts[i]);
    if (got[i] != want) {
      std::cerr << "MISMATCH i=" << i << " x=" << pts[i] << " got=" << got[i]
                << " want=" << want << "\n";
      return 1;
    }
  }
  if (got.back() != got[2]) { std::cerr << "duplicate point order broken\n"; return 1; }
  std::cout << "slice ok: ";
  for (auto& v : got) std::cout << v << " ";
  std::cout << "\n";
  return 0;
}
