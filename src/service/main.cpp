#include "binary_io.hpp"
#include "response.hpp"
#include "bench/benchmark.hpp"
#include "common/request.hpp"
#include "contract/numeric_contract.hpp"
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace {

using fft::common::FftStatus;
using fft::common::FftOptions;
using fft::common::RequestContext;
using fft::common::logLine;
using fft::mathcore::Cmplx;

struct Args {
  std::string cmd;
  std::string inPath, outPath;
  std::string requestId;
  std::size_t n = 0;
  std::string kind = "pseudorandom";
  std::size_t param = 0;
  long long seed = 1234567;
  std::size_t refMaxN = 512;
  std::size_t memoryBudget = 0;
};

void usage() {
  std::fputs(
      "bluestein-fft - arbitrary length complex FFT service\n"
      "version " BLUESTEIN_FFT_VERSION_STRING "\n\n"
      "usage:\n"
      "  fft_service fft      --in DATA.cft --out SPEC.cft [--request-id ID]\n"
      "                       [--memory-budget BYTES]\n"
      "  fft_service ifft     --in SPEC.cft --out DATA.cft [options]\n"
      "  fft_service verify   --in DATA.cft [--ref-max-n K]\n"
      "  fft_service generate --kind K --n N --out DATA.cft\n"
      "                       [--param P] [--seed S]\n"
      "  fft_service demo     [--n N]\n"
      "  fft_service bench    [--ref-max-n K]\n\n"
      "fixture kinds: impulse constant tone cosine large-dynamic pseudorandom\n"
      "wire format: CFT1 | uint64 LE length | interleaved float64 LE pairs\n",
      stdout);
}

bool parseArgs(int argc, char** argv, Args& a) {
  if (argc < 2) return false;
  a.cmd = argv[1];
  for (int i = 2; i < argc; ++i) {
    std::string k = argv[i];
    auto need = [&]() -> std::string {
      return (i + 1 < argc) ? argv[++i] : std::string();
    };
    if (k == "--in") a.inPath = need();
    else if (k == "--out") a.outPath = need();
    else if (k == "--request-id") a.requestId = need();
    else if (k == "--n") a.n = std::strtoull(need().c_str(), nullptr, 10);
    else if (k == "--param") a.param = std::strtoull(need().c_str(), nullptr, 10);
    else if (k == "--seed") a.seed = std::strtoll(need().c_str(), nullptr, 10);
    else if (k == "--kind") a.kind = need();
    else if (k == "--ref-max-n") a.refMaxN = std::strtoull(need().c_str(), nullptr, 10);
    else if (k == "--memory-budget")
      a.memoryBudget = std::strtoull(need().c_str(), nullptr, 10);
    else return false;
  }
  if (a.requestId.empty()) a.requestId = RequestContext::generateId();
  return true;
}

fft::bench::FixtureKind parseKind(const std::string& k) {
  using fft::bench::FixtureKind;
  if (k == "impulse") return FixtureKind::Impulse;
  if (k == "constant") return FixtureKind::Constant;
  if (k == "tone") return FixtureKind::ComplexTone;
  if (k == "cosine") return FixtureKind::CosineReal;
  if (k == "large-dynamic") return FixtureKind::LargeDynamic;
  return FixtureKind::PseudoRandom;
}

fft::service::Response statusResponse(const Args& a, FftStatus s,
                                      const std::string& reason) {
  fft::service::Response r;
  r.requestId = a.requestId;
  r.status = s;
  r.failureReason = reason;
  return r;
}

int runTransform(const Args& a, int sign) {
  RequestContext ctx{a.requestId, BLUESTEIN_FFT_CORE_NAME,
                           BLUESTEIN_FFT_VERSION_STRING, ""};
  std::vector<Cmplx> x;
  std::string err;
  if (!fft::service::readVector(x, a.inPath, err)) {
    logLine(ctx, "ERROR", err);
    auto r = statusResponse(a, FftStatus::InvalidArgument, err);
    r.emitJson(stdout, true);
    return 2;
  }
  ctx.location = sign == -1 ? "service.fft" : "service.ifft";
  logLine(ctx, "INFO", "received transform request N=" +
                           std::to_string(x.size()));
  FftOptions opt;
  opt.memoryBudgetBytes = a.memoryBudget;
  auto res = fft::mathcore::transform(x, sign, opt);
  fft::service::Response r;
  r.requestId = a.requestId;
  r.status = res.error.status;
  r.kernelPath = res.kernelPath;
  r.length = res.usedLength;
  r.convolutionLength = res.convolutionLength;
  r.peakWorkspaceBytes = res.peakWorkspaceBytes;
  r.detail = res.error.message;
  if (res.error.status != FftStatus::Ok) {
    r.failureReason = res.error.message;
    logLine(ctx, "ERROR", std::string("transform failed: ") + res.error.message);
    r.emitJson(stdout, true);
    return 3;
  }
  if (!a.outPath.empty() &&
      !fft::service::writeVector(res.out, a.outPath, err)) {
    r.status = FftStatus::InvalidArgument;
    r.failureReason = err;
    r.emitJson(stdout, true);
    return 2;
  }
  // Fixed normalization self-check: after a forward transform reconstruct
  // with ifft; after an inverse transform reconstruct with fft (no scale).
  if (sign == -1) {
    auto back = fft::mathcore::ifft(res.out);
    r.roundTripError = fft::contract::roundTripError(x, back.out);
  } else {
    auto back = fft::mathcore::fft(res.out);
    r.roundTripError = fft::contract::roundTripError(x, back.out);
  }
  ctx.location = std::string(res.kernelPath) + ":L=" +
                 std::to_string(res.convolutionLength);
  logLine(ctx, "INFO", std::string("transform ok via ") + res.kernelPath +
                           " convL=" +
                           std::to_string(res.convolutionLength) +
                           " workspaceBytes=" +
                           std::to_string(res.peakWorkspaceBytes));
  r.emitJson(stdout, true);
  return 0;
}

int runVerify(const Args& a) {
  RequestContext ctx{a.requestId, "contract.verifier",
                           BLUESTEIN_FFT_VERSION_STRING, ""};
  std::vector<Cmplx> x;
  std::string err;
  if (!fft::service::readVector(x, a.inPath, err)) {
    auto r = statusResponse(a, FftStatus::InvalidArgument, err);
    r.emitJson(stdout, true);
    return 2;
  }
  auto got = fft::mathcore::fft(x);
  fft::service::Response r;
  r.requestId = a.requestId;
  r.length = got.usedLength;
  r.convolutionLength = got.convolutionLength;
  r.kernelPath = got.kernelPath;
  r.peakWorkspaceBytes = got.peakWorkspaceBytes;
  if (got.error.status != FftStatus::Ok) {
    r.status = got.error.status;
    r.failureReason = got.error.message;
    r.emitJson(stdout, true);
    return 3;
  }
  if (x.size() > a.refMaxN) {
    r.uncertain = true;
    r.status = FftStatus::PrecisionRisk;
    r.failureReason =
        "N exceeds independent reference limit; only structural/round-trip "
        "checks were possible (uncertain, not a numeric acceptance)";
    auto back = fft::mathcore::ifft(got.out);
    r.roundTripError = fft::contract::roundTripError(x, back.out);
    logLine(ctx, "WARN", r.failureReason);
    r.emitJson(stdout, true);
    return 4;
  }
  fft::reference::DirectDftOptions ro;
  auto refr = fft::reference::directDftDouble(x, -1, ro);
  auto met = fft::contract::measureAgainstReference(got.out, refr.out);
  auto verdict = fft::contract::judge(met);
  r.status = verdict.category;
  r.uncertain = verdict.uncertain;
  r.maxAbsError = met.maxAbsError;
  r.detail = verdict.summary;
  r.failureReason = verdict.failureReason;
  auto geom = fft::contract::checkConvolutionGeometry(
      x.size(), got.convolutionLength);
  if (!geom.linearConvolutionSatisfied) {
    r.status = FftStatus::ReferenceMismatch;
    r.failureReason = geom.detail;
  }
  if (!r.failureReason.empty()) logLine(ctx, "WARN", r.failureReason);
  r.emitJson(stdout, true);
  return r.status == FftStatus::Ok ? 0 : 4;
}

int runGenerate(const Args& a) {
  if (a.n == 0) {
    auto r = statusResponse(a, FftStatus::InvalidLength, "--n must be >= 1");
    r.emitJson(stdout, true);
    return 2;
  }
  auto fx = fft::bench::generateFixture(parseKind(a.kind), a.n, a.seed,
                                        a.param);
  fx.description = a.kind + ": " + fx.description;
  std::string err;
  if (!fft::service::writeVector(fx.samples, a.outPath, err)) {
    auto r = statusResponse(a, FftStatus::InvalidArgument, err);
    r.emitJson(stdout, true);
    return 2;
  }
  fft::service::Response r;
  r.requestId = a.requestId;
  r.length = a.n;
  r.detail = fx.description;
  r.emitJson(stdout, true);
  return 0;
}

int runDemo(const Args& a) {
  const std::size_t n = a.n ? a.n : 13; // 13 is prime -> Bluestein
  RequestContext ctx{a.requestId, "service.demo",
                           BLUESTEIN_FFT_VERSION_STRING, ""};
  logLine(ctx, "INFO", "demo start prime N=" + std::to_string(n));
  std::vector<Cmplx> x(n);
  for (std::size_t i = 0; i < n; ++i)
    x[i] = Cmplx(std::cos(0.31 * i + 0.7), std::sin(0.17 * i - 0.4));
  auto fwd = fft::mathcore::fft(x);
  auto refr = fft::reference::directDftDouble(x, -1);
  auto met = fft::contract::measureAgainstReference(fwd.out, refr.out);
  auto back = fft::mathcore::ifft(fwd.out);
  double rt = fft::contract::roundTripError(x, back.out);
  auto geom = fft::contract::checkConvolutionGeometry(n,
                                                      fwd.convolutionLength);
  std::printf("demo requestId=%s version=%s\n", a.requestId.c_str(),
              BLUESTEIN_FFT_VERSION_STRING);
  std::printf("N=%zu (prime=%d) kernel=%s convolutionL=%zu\n", n,
              n == 13 ? 1 : 0, fwd.kernelPath, fwd.convolutionLength);
  std::printf("geometry: %s -> linear convolution satisfied=%d\n",
              geom.detail.c_str(), geom.linearConvolutionSatisfied);
  std::printf("vs independent long-double DFT: maxAbs=%.6g rms=%.6g "
              "worstBin=%zu\n", met.maxAbsError, met.rmsAbsError,
              met.worstBin);
  std::printf("round trip maxAbs=%.6g workspacePeakBytes=%zu\n", rt,
              fwd.peakWorkspaceBytes);
  logLine(ctx, "INFO", "demo complete");
  return met.maxAbsError <= 1e-8 && rt <= 1e-9 ? 0 : 4;
}

int runBench(const Args& a) {
  const std::vector<std::size_t> lens = {1, 2, 7, 13, 64, 100, 257, 1000,
                                         4096, 5003};
  auto rep = fft::bench::runBenchmark(lens, a.refMaxN, a.requestId);
  std::printf("benchmark requestId=%s version=%s\n", rep.requestId.c_str(),
              rep.version.c_str());
  std::printf("RSS before=%ld kB after=%ld kB\n", rep.rssBeforeBytes / 1024,
              rep.rssAfterBytes / 1024);
  std::printf("%-6s %-10s %-8s %-10s %-12s %-10s %-14s %s\n", "N",
              "kernel", "convL", "fwd(ms)", "inv(ms)", "ws(KB)",
              "vsRef", "roundTrip");
  int bad = 0;
  for (const auto& r : rep.records) {
    if (r.status != FftStatus::Ok) ++bad;
    std::printf("%-6zu %-10s %-8zu %-10.4f %-12.4f %-10zu %-14.3g %.3g",
                r.n, r.kernelPath, r.convolutionLength, r.forwardMs,
                r.inverseMs, r.peakWorkspaceBytes / 1024,
                r.maxAbsVsReference, r.roundTripError);
    if (!r.note.empty()) std::printf("  NOTE: %s", r.note.c_str());
    std::printf("\n");
  }
  return bad == 0 ? 0 : 4;
}

} // namespace

int main(int argc, char** argv) {
  Args a;
  if (!parseArgs(argc, argv, a)) {
    usage();
    return 2;
  }
  if (a.cmd == "fft") return runTransform(a, -1);
  if (a.cmd == "ifft") return runTransform(a, +1);
  if (a.cmd == "verify") return runVerify(a);
  if (a.cmd == "generate") return runGenerate(a);
  if (a.cmd == "demo") return runDemo(a);
  if (a.cmd == "bench") return runBench(a);
  usage();
  return 2;
}
