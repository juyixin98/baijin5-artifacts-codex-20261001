#pragma once
// Chirp phase evaluation with controlled large-index error.
// Returns exp(sign * i * pi * n^2 / N).
#include <complex>
#include <cstddef>
#include <cstdint>

namespace fft::mathcore {

std::complex<double> chirpAt(std::int64_t n, std::size_t N, int sign);

} // namespace fft::mathcore
