#pragma once

#include <Eigen/Dense>

#include <cstdint>
#include <chrono>
#include <string>
#include <vector>

// Independent benchmark support: synthetic workload generation and timing.
// Nothing here uses the core solver; the benchmark app feeds these workloads
// to procrustes::fit and reports throughput and residual stability.
namespace procrustes::bench {

struct Workload {
  std::string name;
  int dimension = 0;
  int point_count = 0;
  Eigen::MatrixXd source;
  Eigen::MatrixXd target;
  Eigen::VectorXd weights;
};

// Deterministic PRNG so benchmarks and data generation are reproducible.
class SplitMix64 {
 public:
  explicit SplitMix64(std::uint64_t seed) : state_(seed) {}

  std::uint64_t nextU64() {
    std::uint64_t z = (state_ += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
  }

  // Uniform double in (-1, 1).
  double nextUnit() {
    return static_cast<double>(static_cast<std::int64_t>(nextU64())) /
           static_cast<double>(1ULL << 63);
  }

 private:
  std::uint64_t state_;
};

// Generates a source cloud (uniform box), a random proper rotation (seeded),
// a translation and scale, and an exact transformed target cloud.
Workload makeRigidWorkload(int dimension, int point_count,
                           std::uint64_t seed = 0xC0FFEE);
Workload makeSimilarityWorkload(int dimension, int point_count,
                                double scale = 2.5,
                                std::uint64_t seed = 0xBEEF);

// Collinear cloud (all points on one axis) for non-uniqueness benchmarks.
Workload makeCollinearWorkload(int dimension, int point_count,
                               std::uint64_t seed = 0x1234);

struct TimingStats {
  int iterations = 0;
  double total_ms = 0.0;
  double mean_ms = 0.0;
  double min_ms = 0.0;
  double max_ms = 0.0;
};

class Stopwatch {
 public:
  void reset() { start_ = clock::now(); }
  double elapsedMs() const {
    std::chrono::duration<double, std::milli> dt = clock::now() - start_;
    return dt.count();
  }

 private:
  using clock = std::chrono::steady_clock;
  clock::time_point start_ = clock::now();
};

}  // namespace procrustes::bench
