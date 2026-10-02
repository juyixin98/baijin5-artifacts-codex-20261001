#pragma once
// Analytic tree-memory budgeting and deterministic batching.
//
// The product/remainder tree for a batch of b points stores, at level L
// (level 0 = linear leaf factors), polynomials whose total coefficient count
// is b+1 at every level up to ceil(log2 b). The root level, the incoming
// dividend f, and the descending remainder chain each add b+1 slots. This
// planner turns that slot count into a conservative byte estimate and picks
// the largest feasible batch size, so a memory ceiling that cannot hold the
// whole point set transparently splits the job into several batches. Result
// order is reassembled from original point indices, making batches
// unobservable in the output.
#include <cstdint>
#include <vector>
#include <cstddef>

namespace mp::contract { struct Config; }

namespace mp::core {

struct BatchPlan {
  std::vector<size_t> sizes;      // points per batch, in processing order
  size_t batches{0};

  uint64_t limit_bytes{0};
  uint64_t estimated_bytes{0};    // estimate for the largest batch
  size_t max_batch_size{0};
  double bytes_per_slot{0};
  double fudge{1};
  bool feasible{true};
};

// Conservative coefficient slots alive simultaneously for a batch of b
// points: all product-tree levels (L*(b+1)), the root and the dividend copy
// (2*(b+1)), plus the current descending remainder (b+1). Linear leaves are
// shared with level 0.
uint64_t tree_slot_estimate(size_t b) noexcept;

uint64_t tree_bytes(size_t b, double bytes_per_slot, double fudge) noexcept;

// Largest batch size fitting the limit; guarantees >= 1 on any feasible
// configuration. Returns 0 only when a single point does not fit.
size_t max_fitting_batch_size(const contract::Config& cfg) noexcept;

BatchPlan plan_batches(size_t point_count, const contract::Config& cfg) noexcept;

} // namespace mp::core
