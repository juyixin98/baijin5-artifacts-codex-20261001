#pragma once
#include "numcontract/failure.h"
#include <cstdint>
#include <string>
#include <vector>

namespace mp::contract {

// Backend configuration (INI). Every key is optional and has a documented
// default in config/default.ini.
struct Config {
  // Per-job tree memory ceiling (bytes). One batch must fit under this.
  uint64_t memory_limit_bytes{64ull * 1024ull * 1024ull};
  // Estimated bytes per stored coefficient slot (struct + ring digit headroom).
  double bytes_per_slot{24.0};
  // Conservative safety multiplier on the analytic tree-memory estimate.
  double memory_fudge{2.0};
  // Maximum number of points a single batch is allowed to contain (0 = auto).
  uint64_t max_batch_points{0};
};

// Parse INI text. Lines are `key = value`; `#`/`;` start comments.
// Returns failures with stable categories on unknown keys / bad values.
std::vector<Failure> parse_config(const std::string& ini, Config& out);

// Resolve a byte size literal such as "64MiB", "1024KiB", "500000", "1GB".
bool parse_size_bytes(const std::string& text, uint64_t& out_bytes) noexcept;

} // namespace mp::contract
