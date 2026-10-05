#pragma once

// Independent reference implementation: Golub-Welsch algorithm.
//
// Gauss-Legendre nodes are the eigenvalues of the n x n symmetric
// tridiagonal Jacobi matrix (zero diagonal, off-diagonal
// beta_k = k / sqrt(4k^2 - 1)); the weights are 2 * v_{1,i}^2 where v_1 is
// the first row of the eigenvector matrix. This shares no code with the
// Newton-based kernel, so agreement between the two is genuine
// cross-validation. It costs O(n^3) and is used only as a benchmark.

#include <cstddef>
#include <vector>

#include "gauss/error.hpp"

namespace gauss::benchmark {

struct RawRule {
  std::vector<double> nodes;  // ascending
  std::vector<double> weights;
};

//   order == 0            -> kInvalidInput
//   eigensolver failure   -> kComputationFailure
//   allocation failure    -> kResourceExhaustion
Result<RawRule> golub_welsch_legendre(std::size_t order);

struct CrossCheck {
  std::size_t order = 0;
  double max_node_abs_diff = 0.0;
  double max_weight_abs_diff = 0.0;
  double node_tolerance = 0.0;
  double weight_tolerance = 0.0;
  bool passed = false;
  double kernel_seconds = 0.0;
  double golub_welsch_seconds = 0.0;
};

// Builds the same rule with both algorithms and compares them.
CrossCheck cross_check_legendre(std::size_t order);

}  // namespace gauss::benchmark
