#include "test_support/test_framework.hpp"

#include "procrustes/explain.hpp"
#include "procrustes/fit.hpp"

#include <array>
#include <cstdlib>
#include <cstdio>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef PROCUSTES_DATA_DIR
#error "PROCUSTES_DATA_DIR must be defined"
#endif

namespace {

// Independent fixture parser (separate implementation from apps/support):
// keeps the end-to-end test honest about the on-disk contract.
struct Fixture {
  int dimension = 0;
  Eigen::MatrixXd source;
  Eigen::MatrixXd target;
  Eigen::VectorXd weights;
};

std::vector<std::string> splitComma(const std::string& line) {
  std::vector<std::string> out;
  std::string cur;
  for (char c : line) {
    if (c == ',') {
      out.push_back(cur);
      cur.clear();
    } else {
      cur.push_back(c);
    }
  }
  out.push_back(cur);
  return out;
}

Fixture loadFixture(const std::string& path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("missing fixture: " + path);
  std::vector<std::array<double, 7>> rows;
  std::string line;
  int d = 0;
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    auto fields = splitComma(line);
    if (d == 0) d = (static_cast<int>(fields.size()) - 1) / 2;
    if (!(d == 2 || d == 3)) {
      throw std::runtime_error("integration fixtures are 2D or 3D");
    }
    std::array<double, 7> row{};
    for (size_t i = 0; i < fields.size(); ++i) row[i] = std::stod(fields[i]);
    rows.push_back(row);
  }
  Fixture fx;
  fx.dimension = d;
  const int n = static_cast<int>(rows.size());
  fx.source = Eigen::MatrixXd(d, n);
  fx.target = Eigen::MatrixXd(d, n);
  fx.weights = Eigen::VectorXd(n);
  for (int i = 0; i < n; ++i) {
    for (int k = 0; k < d; ++k) {
      fx.source(k, i) = rows[i][k];
      fx.target(k, i) = rows[i][d + k];
    }
    fx.weights(i) = rows[i][2 * d];
  }
  return fx;
}

procrustes::Request toRequest(const Fixture& fx,
                              procrustes::TransformMode mode,
                              const std::string& id) {
  procrustes::Request req;
  req.request_id = id;
  req.source = fx.source;
  req.target = fx.target;
  req.weights = fx.weights;
  req.mode = mode;
  return req;
}

std::string runCli(const std::string& args, int& exit_code) {
  // Resolve the CLI path from an env var provided by CTest, falling back to
  // the conventional build location.
  const char* env = std::getenv("PROCRUSTES_CLI");
  std::string cli = env ? env
      : std::string(PROCUSTES_CLI_FALLBACK);
  std::string cmd = cli + " " + args + " 2>/dev/null";
  std::array<char, 256> buffer{};
  std::string output;
  FILE* pipe = popen(cmd.c_str(), "r");
  if (!pipe) throw std::runtime_error("failed to launch CLI");
  while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe)) {
    output += buffer.data();
  }
  const int status = pclose(pipe);
  exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
  return output;
}

}  // namespace

// End-to-end: regenerated sample fixtures fit to hand-selected truth values.
TEST_CASE("integration/rigid2d-exact-fixture") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/rigid2d_exact.csv");
  CHECK(fx.dimension == 2);
  auto result = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalProper,
                "int-rigid2d"));
  CHECK(result.ok);
  CHECK_CLOSE(result.determinant, 1.0, 1e-12);
  CHECK_CLOSE(result.rmse, 0.0, 1e-10);
  Eigen::Matrix2d R;
  R << 0, -1, 1, 0;
  CHECK((result.rotation - R).cwiseAbs().maxCoeff() < 1e-10);
  CHECK_CLOSE(result.translation(0), 2.0, 1e-10);
  CHECK_CLOSE(result.translation(1), -3.0, 1e-10);
}

TEST_CASE("integration/similarity3d-exact-fixture") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/similarity3d_exact.csv");
  CHECK(fx.dimension == 3);
  auto result = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::SimilarityProper,
                "int-sim3d"));
  CHECK(result.ok);
  CHECK_CLOSE(result.scale, 2.0, 1e-9);
  CHECK_CLOSE(result.determinant, 1.0, 1e-12);
  CHECK(result.rmse < 1e-9);
  CHECK_CLOSE(result.translation(0), 1.0, 1e-9);
  CHECK_CLOSE(result.translation(1), -2.0, 1e-9);
  CHECK_CLOSE(result.translation(2), 3.0, 1e-9);
}

TEST_CASE("integration/reflection2d-fixture-mode-separation") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/reflection2d_exact.csv");
  auto allow = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalAllowReflection,
                "int-reflect-allow"));
  CHECK(allow.ok);
  CHECK_CLOSE(allow.determinant, -1.0, 1e-12);
  CHECK(allow.rmse < 1e-9);

  auto proper = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalProper,
                "int-reflect-proper"));
  CHECK(proper.ok);
  CHECK_CLOSE(proper.determinant, 1.0, 1e-12);
  CHECK_CLOSE(proper.sse, 4.0, 1e-9);  // hand-derived for unit square
}

TEST_CASE("integration/collinear2d-fixture-multiple-solutions") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/collinear2d_reflect.csv");
  auto allow = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalAllowReflection,
                "int-col2d-allow"));
  CHECK(allow.ok);
  CHECK(!allow.rotation_unique);
  CHECK(allow.rmse < 1e-9);

  auto proper = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalProper,
                "int-col2d-proper"));
  CHECK(proper.ok);
  CHECK(proper.rotation_unique);
  CHECK(proper.rmse < 1e-9);
}

TEST_CASE("integration/collinear3d-fixture-nonunique") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/collinear3d_proper.csv");
  auto result = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalProper,
                "int-col3d"));
  CHECK(result.ok);
  CHECK(!result.rotation_unique);
  CHECK(result.rmse < 1e-9);
  bool flagged = false;
  for (const auto& dg : result.diagnostics) {
    if (dg.code == procrustes::FailureCode::SingularNonUnique) flagged = true;
  }
  CHECK(flagged);
}

TEST_CASE("integration/coincident-source-fixture-scale-unidentifiable") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/coincident_source2d.csv");
  auto result = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::SimilarityProper,
                "int-coincident"));
  CHECK(result.ok);
  CHECK(!result.scale_identifiable);
  CHECK(!result.rotation_unique);
}

TEST_CASE("integration/weighted-noisy-fixture-concrete-rmse") {
  const auto fx = loadFixture(PROCUSTES_DATA_DIR "/weighted_noisy2d.csv");
  auto result = procrustes::fit(
      toRequest(fx, procrustes::TransformMode::OrthogonalProper,
                "int-noisy"));
  CHECK(result.ok);
  CHECK_CLOSE(result.determinant, 1.0, 1e-12);
  // Concrete value produced by the fixture's documented noise realization;
  // regression value verified against an independent 2D angle scan.
  CHECK_CLOSE(result.sse, 0.1826389, 1e-5);
  CHECK_CLOSE(result.rmse, 0.1744703, 1e-5);
}

TEST_CASE("integration/cli-json-success-carries-identity") {
  int code = 0;
  std::string json = runCli(
      "--csv " PROCUSTES_DATA_DIR
      "/rigid2d_exact.csv --mode rigid --request-id int-cli-ok --format json",
      code);
  CHECK(code == 0);
  CHECK(json.find("\"request_id\": \"int-cli-ok\"") != std::string::npos);
  CHECK(json.find("\"ok\": true") != std::string::npos);
  CHECK(json.find("\"version\": \"1.0.0\"") != std::string::npos);
  CHECK(json.find("\"rmse\": 0") != std::string::npos);
}

TEST_CASE("integration/cli-failure-exit-code-and-category") {
  const std::string bad = "bad_zero_weight_integration.csv";
  {
    std::ofstream out(bad);
    out << "0,0,1,1,0\n1,0,2,1,0\n";
  }
  int code = 0;
  std::string text = runCli(
      "--csv " + bad + " --mode rigid --request-id int-cli-fail", code);
  CHECK(code == 1);
  CHECK(text.find("FAILED") != std::string::npos);
  CHECK(text.find("total-weight-zero") != std::string::npos);
  CHECK(text.find("int-cli-fail") != std::string::npos);
}
