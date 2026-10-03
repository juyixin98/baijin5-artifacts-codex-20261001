#pragma once
// Minimal Eigen NumTraits bridges for the two kernel scalar domains:
//  - field::ModInt  (exact modular integer, fixed width)
//  - boost::multiprecision::cpp_int (unbounded exact integer)
// Only the arithmetic actually exercised by the kernel (add/sub/mul of
// integers, segment cwiseProduct) is required.
#include <Eigen/Dense>
#include <boost/multiprecision/cpp_int.hpp>

#include "polyeval/field.hpp"

namespace Eigen {

template <>
struct NumTraits<polyeval::field::ModInt>
    : GenericNumTraits<polyeval::field::ModInt> {
  enum {
    IsInteger = 1,
    IsSigned = 0,
    IsComplex = 0,
    RequireInitialization = 1,
    ReadCost = 2,
    AddCost = 2,
    MulCost = 4
  };
};

template <>
struct NumTraits<boost::multiprecision::cpp_int>
    : GenericNumTraits<boost::multiprecision::cpp_int> {
  enum {
    IsInteger = 1,
    IsSigned = 1,
    IsComplex = 0,
    RequireInitialization = 1,
    ReadCost = HugeCost,
    AddCost = HugeCost,
    MulCost = HugeCost
  };
};

}  // namespace Eigen
