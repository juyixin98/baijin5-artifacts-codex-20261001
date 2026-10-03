// Acceptance: runs must report memory (workspace estimate and process RSS),
// and reports must be positive/consistent and grow with problem size.
#include "mathcore/fft.hpp"
#include "bench/benchmark.hpp"
#include "test_framework.hpp"
#include <cmath>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;

int main() {
  tf::begin("workspace bytes reported and structurally consistent");
  {
    std::vector<Cmplx> x(1000, Cmplx(1, 0));
    auto r = mc::fft(x);
    tf::check(r.peakWorkspaceBytes > 0, "workspace must be > 0");
    // Lower bound: at least the N input and N chirp and 2L FFT vectors.
    std::size_t expectedMin = (2 * 1000 + 2 * r.convolutionLength) *
                              sizeof(Cmplx);
    tf::check(r.peakWorkspaceBytes == expectedMin,
              "workspace accounting mismatch");
  }

  tf::begin("RSS sampler returns a plausible value on Linux");
  {
    auto m = fft::bench::sampleMemory();
    tf::check(m.rssBytesNow > 1024, "RSS should exceed 1 KiB");
  }

  tf::begin("benchmark report includes RSS before/after and per-record ws");
  {
    auto rep = fft::bench::runBenchmark({64, 5003}, 64, "req-test-mem");
    tf::check(rep.records.size() == 2, "two records");
    tf::check(rep.rssBeforeBytes > 0 && rep.rssAfterBytes > 0, "rss");
    tf::check(rep.records[0].peakWorkspaceBytes > 0, "ws rec0");
    tf::check(rep.records[1].peakWorkspaceBytes >
                  rep.records[0].peakWorkspaceBytes,
              "larger problem uses more workspace");
  }
  return tf::finish();
}
