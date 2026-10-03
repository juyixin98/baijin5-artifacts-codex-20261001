// End-to-end acceptance through the real service binary and its on-disk
// binary wire format: generate -> fft -> ifft -> reconstruct.
#include "test_framework.hpp"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <complex>
#include <cstdint>
#include <cstring>

namespace {

std::string exePath() {
  // CMake passes the built binary directory via TEST_BINARY_DIR.
#ifndef TEST_BINARY_DIR
#define TEST_BINARY_DIR "."
#endif
  return std::string(TEST_BINARY_DIR) + "/fft_service";
}

int run(const std::string& args, std::string& out) {
  std::string cmd = exePath() + " " + args + " 2>/dev/null";
  std::FILE* p = popen(cmd.c_str(), "r");
  if (!p) return -1;
  char buf[512];
  while (std::fgets(buf, sizeof(buf), p)) out += buf;
  return pclose(p) / 256;
}

bool readCft(const std::string& path, std::vector<std::complex<double>>& v) {
  std::ifstream f(path, std::ios::binary);
  if (!f) return false;
  char magic[4];
  f.read(magic, 4);
  if (std::memcmp(magic, "CFT1", 4) != 0) return false;
  std::uint64_t n = 0;
  unsigned char lb[8];
  f.read(reinterpret_cast<char*>(lb), 8);
  for (int i = 0; i < 8; ++i) n |= std::uint64_t(lb[i]) << (8 * i);
  v.resize(n);
  for (std::uint64_t i = 0; i < n; ++i) {
    double re, im;
    f.read(reinterpret_cast<char*>(&re), 8);
    f.read(reinterpret_cast<char*>(&im), 8);
    v[i] = {re, im};
  }
  return bool(f);
}

} // namespace

int main() {
  const std::string inF = "cli_pipeline_in.cft";
  const std::string specF = "cli_pipeline_spec.cft";
  const std::string backF = "cli_pipeline_back.cft";

  tf::begin("generate prime-length fixture via service");
  {
    std::string out;
    int rc = run("generate --kind tone --n 53 --param 4 --seed 1 --out " +
                     inF + " --request-id cli-gen",
                 out);
    tf::check(rc == 0, "generate exit code");
    tf::check(out.find("\"status\": \"OK\"") != std::string::npos,
              "generate response OK");
  }

  tf::begin("forward transform reports Bluestein kernel and conv length");
  {
    std::string out;
    int rc = run("fft --in " + inF + " --out " + specF +
                     " --request-id cli-fft",
                 out);
    tf::check(rc == 0, "fft exit code");
    tf::check(out.find("\"kernelPath\": \"bluestein\"") !=
                  std::string::npos,
              "expected bluestein kernel");
    tf::check(out.find("\"convolutionLength\": 128") !=
                  std::string::npos,
              "2*53-1=105 -> L=128");
  }

  tf::begin("inverse reconstructs the original file on disk");
  {
    std::string out;
    int rc = run("ifft --in " + specF + " --out " + backF +
                     " --request-id cli-ifft",
                 out);
    tf::check(rc == 0, "ifft exit code");
    std::vector<std::complex<double>> a, b;
    bool ok1 = readCft(inF, a);
    bool ok2 = readCft(backF, b);
    tf::check(ok1 && ok2, "both CFT1 files readable");
    tf::check(a.size() == b.size() && a.size() == 53, "lengths match");
    double e = 0;
    for (std::size_t i = 0; i < a.size(); ++i)
      e = std::max(e, std::abs(a[i] - b[i]));
    tf::check(e <= 1e-10, "on-disk round trip error " + std::to_string(e));
  }

  tf::begin("verify beyond reference limit reports uncertainty, not pass");
  {
    std::string out;
    int rc = run("verify --in " + inF + " --ref-max-n 10 --request-id cli-v",
                 out);
    tf::check(rc == 4, "uncertain verify must exit 4");
    tf::check(out.find("\"status\": \"PRECISION_RISK\"") !=
                  std::string::npos,
              "must label PRECISION_RISK");
    tf::check(out.find("\"uncertain\": true") != std::string::npos,
              "must be marked uncertain");
  }

  tf::begin("missing input file is a concrete INVALID_ARGUMENT failure");
  {
    std::string out;
    int rc = run("fft --in does_not_exist.cft --request-id cli-missing", out);
    tf::check(rc != 0, "must fail");
    tf::check(out.find("\"status\": \"INVALID_ARGUMENT\"") !=
                  std::string::npos,
              "missing file must be INVALID_ARGUMENT");
  }

  std::remove(inF.c_str());
  std::remove(specF.c_str());
  std::remove(backF.c_str());
  return tf::finish();
}
