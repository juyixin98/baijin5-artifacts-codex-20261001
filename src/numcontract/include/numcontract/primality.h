#pragma once
#include <cstdint>
namespace mp::contract {
// Deterministic Miller-Rabin for every 64-bit unsigned integer.
bool is_prime(uint64_t n) noexcept;
} // namespace mp::contract
