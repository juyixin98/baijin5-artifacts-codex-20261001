// Integration test: reads the synthetic CSV fixtures produced by the
// independent generator plus their ground-truth sidecars, runs the kernel
// through the same public entry point as the CLI, and asserts concrete
// recovered transforms and outcome categories. Ground truth is parsed from
// the generator's sidecar files, not from kernel output.
#include "procrustes/contracts.hpp"
#include "procrustes/io_csv.hpp"
#include "procrustes/procrustes.hpp"
#include "../unit/test_macros.hpp"

#include <fstream>
#include <map>
#include <sstream>

using namespace procrustes;

namespace {

std::map<std::string, std::string> parse_truth(const std::string& path) {
  std::map<std::string, std::string> kv;
  std::ifstream f(path);
  std::string line;
  while (std::getline(f, line)) {
    const size_t h = line.find('#');
    if (h != std::string::npos) line.erase(h);
    const size_t eq = line.find('=');
    if (eq == std::string::npos) continue;
    auto trim = [](std::string s) {
      size_t a = s.find_first_not_of(" \t\r");
      if (a == std::string::npos) return std::string();
      size_t b = s.find_last_not_of(" \t\r");
      return s.substr(a, b - a + 1);
    };
    // strip inline comments in values
    std::string key = trim(line.substr(0, eq));
    std::string val = trim(line.substr(eq + 1));
    if (const size_t sp = val.find(' '); sp != std::string::npos &&
                                        (key.rfind("R", 0) == 0 ||
                                         key.rfind("t", 0) == 0 ||
                                         key == "scale" || key == "rms")) {
      // leave numeric prefix only for numeric keys that carry annotations
      if (key != "scenario" && key != "mode" && key != "reflection" &&
          key != "rotation_only" && key != "reflection_allowed")
        val = val.substr(0, sp);
    }
    kv[key] = val;
  }
  return kv;
}

void check_rigid2d(const std::string& data_dir) {
  auto loaded = io::load_csv(data_dir + "/rigid2d_pairs.csv");
  CHECK_MSG(loaded.error.empty(), loaded.error);
  auto truth = parse_truth(data_dir + "/rigid2d_truth.txt");
  FitResult r = fit(loaded.points, FitConfig{}, RequestContext{"it-rigid2d"});
  CHECK(r.status == Status::Success);
  CHECK_CLOSE(r.rotation(0, 0), std::stod(truth.at("R00")), 1e-9);
  CHECK_CLOSE(r.rotation(0, 1), std::stod(truth.at("R01")), 1e-9);
  CHECK_CLOSE(r.rotation(1, 0), std::stod(truth.at("R10")), 1e-9);
  CHECK_CLOSE(r.rotation(1, 1), std::stod(truth.at("R11")), 1e-9);
  CHECK_CLOSE(r.translation(0), std::stod(truth.at("t0")), 1e-9);
  CHECK_CLOSE(r.translation(1), std::stod(truth.at("t1")), 1e-9);
  CHECK_CLOSE(r.rms, 0.0, 1e-9);
  CHECK_CLOSE(r.rotation.determinant(), 1.0, 1e-10);
  auto post = contracts::verify_postconditions(loaded.points, r, FitConfig{});
  CHECK(post.ok);
}

void check_similarity2d(const std::string& data_dir) {
  auto loaded = io::load_csv(data_dir + "/similarity2d_pairs.csv");
  CHECK_MSG(loaded.error.empty(), loaded.error);
  auto truth = parse_truth(data_dir + "/similarity2d_truth.txt");
  FitConfig cfg;
  cfg.estimate_scale = true;
  FitResult r = fit(loaded.points, cfg, RequestContext{"it-simil2d"});
  CHECK(r.status == Status::Success);
  CHECK_CLOSE(r.scale, std::stod(truth.at("scale")), 1e-9);
  CHECK_CLOSE(r.translation(0), std::stod(truth.at("t0")), 1e-9);
  CHECK_CLOSE(r.translation(1), std::stod(truth.at("t1")), 1e-9);
  CHECK_CLOSE(r.rotation.determinant(), 1.0, 1e-10);
  CHECK_CLOSE(r.rms, 0.0, 1e-9);
  auto post = contracts::verify_postconditions(loaded.points, r, cfg);
  CHECK(post.ok);
}

void check_rigid3d(const std::string& data_dir) {
  auto loaded = io::load_csv(data_dir + "/rigid3d_pairs.csv");
  CHECK_MSG(loaded.error.empty(), loaded.error);
  auto truth = parse_truth(data_dir + "/rigid3d_truth.txt");
  FitResult r = fit(loaded.points, FitConfig{}, RequestContext{"it-rigid3d"});
  CHECK(r.status == Status::Success);
  CHECK(r.rank == 3);
  CHECK_CLOSE(r.translation(0), std::stod(truth.at("t0")), 1e-9);
  CHECK_CLOSE(r.translation(1), std::stod(truth.at("t1")), 1e-9);
  CHECK_CLOSE(r.translation(2), std::stod(truth.at("t2")), 1e-9);
  CHECK_CLOSE(r.rotation.determinant(), 1.0, 1e-10);
  // R must be orthogonal and a genuine 120-degree rotation about z
  CHECK_CLOSE(r.rotation(2, 0), 0.0, 1e-10);
  CHECK_CLOSE(r.rotation(2, 1), 0.0, 1e-10);
  CHECK_CLOSE(r.rotation(2, 2), 1.0, 1e-10);
  CHECK_CLOSE(r.rotation(0, 0), -0.5, 1e-9);
  CHECK_CLOSE(r.rotation(1, 0), std::sqrt(3.0) / 2.0, 1e-9);
  CHECK_CLOSE(r.rms, 0.0, 1e-9);
}

void check_collinear2d(const std::string& data_dir) {
  auto loaded = io::load_csv(data_dir + "/collinear2d_pairs.csv");
  CHECK_MSG(loaded.error.empty(), loaded.error);

  FitResult rot = fit(loaded.points, FitConfig{}, RequestContext{"it-col-r"});
  CHECK(rot.status == Status::Success);
  CHECK(rot.rank == 1);
  CHECK_CLOSE(rot.rms, 0.0, 1e-10);
  CHECK_CLOSE(rot.rotation.determinant(), 1.0, 1e-10);
  // +90 degrees
  CHECK_CLOSE(rot.rotation(0, 1), -1.0, 1e-9);
  CHECK_CLOSE(rot.rotation(1, 0), 1.0, 1e-9);

  FitConfig refl;
  refl.allow_reflection = true;
  FitResult refr = fit(loaded.points, refl, RequestContext{"it-col-f"});
  CHECK(refr.status == Status::NonUniqueSolution);
  CHECK(refr.uncertainties.size() >= 1);
  CHECK_CLOSE(refr.rms, 0.0, 1e-10);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: test_end_to_end <data_dir>\n";
    return 2;
  }
  const std::string data_dir = argv[1];
  check_rigid2d(data_dir);
  check_similarity2d(data_dir);
  check_rigid3d(data_dir);
  check_collinear2d(data_dir);
  return proc_test::finish("end_to_end_fixtures");
}
