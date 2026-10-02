// Standalone micro-benchmark for the fit kernel on deterministic synthetic
// workloads. Reports per-workload timing and residual stability.
#include "procrustes/bench.hpp"
#include "procrustes/fit.hpp"
#include "procrustes/logger.hpp"

#include <algorithm>
#include <numeric>
#include <iomanip>
#include <iostream>
#include <vector>

namespace {

template <typename Make>
procrustes::bench::TimingStats runOne(const procrustes::bench::Workload& wl,
                                      procrustes::TransformMode mode,
                                      int iterations, Make makeRequest) {
  using namespace procrustes;
  using namespace procrustes::bench;
  std::vector<double> samples;
  samples.reserve(iterations);
  double rmse = 0.0;
  for (int it = 0; it < iterations; ++it) {
    Request req = makeRequest(wl, mode);
    Stopwatch sw;
    FitResult r = fit(req);
    samples.push_back(sw.elapsedMs());
    rmse = r.rmse;
    if (!r.ok) {
      throw std::runtime_error("benchmark fit failed for " + wl.name);
    }
  }
  TimingStats stats;
  stats.iterations = iterations;
  stats.total_ms = std::accumulate(samples.begin(), samples.end(), 0.0);
  stats.mean_ms = stats.total_ms / iterations;
  stats.min_ms = *std::min_element(samples.begin(), samples.end());
  stats.max_ms = *std::max_element(samples.begin(), samples.end());
  std::cout << "  " << std::left << std::setw(22) << wl.name
            << " n=" << std::setw(6) << wl.point_count
            << " mean=" << std::setw(9) << stats.mean_ms << "ms"
            << " min=" << std::setw(9) << stats.min_ms << "ms"
            << " rmse=" << std::setw(12) << rmse << '\n';
  return stats;
}

}  // namespace

int main(int argc, char** argv) {
  using namespace procrustes;
  using namespace procrustes::bench;
  int iterations = 200;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    if ((a == "--iterations" || a == "-n") && i + 1 < argc) {
      iterations = std::max(1, std::stoi(argv[++i]));
    }
  }
  Logger logger;
  logger.setRequestId("bench-local");
  logger.log("bench_main", "starting synthetic benchmark", Severity::Info,
             "iterations=" + std::to_string(iterations));

  auto makeReq = [](const Workload& wl, TransformMode mode) {
    Request req;
    req.request_id = "bench-" + wl.name;
    req.source = wl.source;
    req.target = wl.target;
    req.weights = wl.weights;
    req.mode = mode;
    return req;
  };

  std::cout << "Weighted Procrustes kernel micro-benchmark (synthetic)\n";
  std::cout << std::setprecision(4);
  const std::vector<int> sizes = {64, 512, 4096};
  std::cout << "[rigid 3d, proper]\n";
  for (int n : sizes) {
    runOne(makeRigidWorkload(3, n, 1000 + n),
           TransformMode::OrthogonalProper, iterations, makeReq);
  }
  std::cout << "[similarity 3d, reflection allowed]\n";
  for (int n : sizes) {
    runOne(makeSimilarityWorkload(3, n, 2.5, 2000 + n),
           TransformMode::SimilarityAllowReflection, iterations, makeReq);
  }
  std::cout << "[collinear 3d degenerate, proper]\n";
  runOne(makeCollinearWorkload(3, 1024, 3000),
         TransformMode::OrthogonalProper, iterations, makeReq);
  logger.log("bench_main", "benchmark complete", Severity::Info);
  return 0;
}
