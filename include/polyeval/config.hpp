#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace polyeval::config {

struct EvalConfig {
  std::uint64_t memory_bytes = 1ull << 20;   // 1 MiB default product-tree budget
  std::uint64_t field_bytes_per_scalar = 8;  // ModInt width
  std::uint64_t exact_bytes_per_scalar = 32; // conservative average for cpp_int
  std::uint64_t crosscheck_bits = 256;       // exact Horner verification cap
  std::uint64_t exact_batch_size = 0;        // 0 => derive from memory budget
  std::uint64_t field_batch_size = 0;
  std::string version = "1.0.0";
};

struct ConfigResult {
  EvalConfig config;
  std::vector<std::string> errors;  // INVALID_CONFIG_VALUE messages
};

ConfigResult load_config(const std::string& text);

}  // namespace polyeval::config
