#include "core/memory.h"
#include "numcontract/config.h"
#include <algorithm>
#include <cmath>

namespace mp::core {

uint64_t tree_slot_estimate(size_t b) noexcept {
  if (b == 0) return 0;
  size_t levels = 1;
  for (size_t w = 1; w < b; w <<= 1) ++levels;
  // levels product layers + root/dividend/remainder working copies.
  double slots = static_cast<double>(levels + 3) * static_cast<double>(b + 1);
  // Allocator/vector capacity slack (up to ~2x on doubling) and a fixed base.
  slots *= 2.0;
  slots += 128.0;
  return static_cast<uint64_t>(slots);
}

uint64_t tree_bytes(size_t b, double bytes_per_slot, double fudge) noexcept {
  double est = static_cast<double>(tree_slot_estimate(b)) *
               bytes_per_slot * fudge;
  return static_cast<uint64_t>(est) + 1024ull;
}

size_t max_fitting_batch_size(const contract::Config& cfg) noexcept {
  auto fits = [&](size_t b) {
    return tree_bytes(b, cfg.bytes_per_slot, cfg.memory_fudge) <=
           cfg.memory_limit_bytes;
  };
  if (!fits(1)) return 0;
  size_t lo = 1, hi = 1;
  while (fits(hi)) {
    if (hi > (1ull << 40)) break;
    hi <<= 1;
  }
  // Binary search largest fitting size in [lo, hi).
  while (lo + 1 < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if (fits(mid)) lo = mid; else hi = mid;
  }
  return lo;
}

BatchPlan plan_batches(size_t point_count, const contract::Config& cfg) noexcept {
  BatchPlan plan;
  plan.limit_bytes = cfg.memory_limit_bytes;
  plan.bytes_per_slot = cfg.bytes_per_slot;
  plan.fudge = cfg.memory_fudge;
  size_t cap = max_fitting_batch_size(cfg);
  if (cap == 0) { plan.feasible = false; return plan; }
  if (cfg.max_batch_points > 0) cap = std::min(cap, static_cast<size_t>(cfg.max_batch_points));
  plan.max_batch_size = cap;
  size_t remaining = point_count;
  while (remaining > 0) {
    size_t take = std::min(cap, remaining);
    plan.sizes.push_back(take);
    remaining -= take;
  }
  plan.batches = plan.sizes.size();
  plan.estimated_bytes = tree_bytes(cap, cfg.bytes_per_slot, cfg.memory_fudge);
  return plan;
}
} // namespace mp::core
