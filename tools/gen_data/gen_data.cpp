// Synthetic paired-data generator with INDEPENDENT hand-written reference
// transforms. It never calls the fitting kernel: reference R, s, t and the
// target points are constructed directly from trigonometry/axis geometry, so
// integration tests have an oracle that the system under test did not create.
//
// Usage: gen_data <scenario> <out_dir>
//   scenario in: rigid2d | similarity2d | rigid3d | collinear2d
#include "procrustes/config_ini.hpp"
#include "procrustes/io_csv.hpp"

#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <numbers>
#include <string>

using namespace procrustes;

namespace fs = std::filesystem;

namespace {

std::string num(double x) {
  std::ostringstream ss;
  ss << std::setprecision(17) << x;
  return ss.str();
}

void write_truth(fs::path p, const std::string& body) {
  std::ofstream f(p);
  f.precision(17);
  f << "# Independent ground truth, hand-generated (not produced by the\n"
       "# fitting kernel). Format: readable key=value text.\n"
    << body;
}

void write_pair_csv(fs::path p, const Eigen::MatrixXd& src,
                    const Eigen::MatrixXd& dst, const Eigen::VectorXd& w) {
  std::string err = io::save_csv(p.string(), {src, dst, w});
  if (!err.empty()) {
    std::fprintf(stderr, "write failed: %s\n", err.c_str());
    std::exit(1);
  }
}

// Hand-written 2D rotation matrix from an angle (independent construction).
Eigen::MatrixXd rot2(double theta) {
  const double c = std::cos(theta), sn = std::sin(theta);
  Eigen::MatrixXd R(2, 2);
  R << c, -sn, sn, c;
  return R;
}

Eigen::MatrixXd rot3_z(double theta) {
  const double c = std::cos(theta), sn = std::sin(theta);
  Eigen::MatrixXd R(3, 3);
  R << c, -sn, 0,
       sn,  c, 0,
       0,   0, 1;
  return R;
}

int gen_rigid2d(const fs::path& dir) {
  Eigen::MatrixXd p(2, 6);
  p << -2, -1, 0, 1, 2, 1.5,
       -1,  1, 2, 0, -2, -2;
  Eigen::VectorXd w(6);
  w << 1, 2, 1, 3, 1, 2;
  const double theta = std::numbers::pi / 6.0;  // +30 degrees, by hand
  const Eigen::MatrixXd R = rot2(theta);
  Eigen::VectorXd t(2);
  t << 4.5, -1.25;
  Eigen::MatrixXd q = (R * p).colwise() + t;
  write_pair_csv(dir / "rigid2d_pairs.csv", p, q, w);
  write_truth(dir / "rigid2d_truth.txt",
              "scenario=rigid2d\nmode=rigid\nreflection=deny\n"
              "theta_rad=" + num(theta) +
              "\nR00=" + num(R(0, 0)) +
              "\nR01=" + num(R(0, 1)) +
              "\nR10=" + num(R(1, 0)) +
              "\nR11=" + num(R(1, 1)) +
              "\nt0=" + num(t(0)) +
              "\nt1=" + num(t(1)) +
              "\nscale=1\nrms=0\n");
  return 0;
}

int gen_similarity2d(const fs::path& dir) {
  Eigen::MatrixXd p(2, 7);
  p << -3, -1, 0, 1, 2, 3, -2,
       -2,  0, 1, 2, 1, -1, 2;
  Eigen::VectorXd w(7);
  w << 2, 1, 1, 2, 1, 3, 2;
  const double theta = -std::numbers::pi / 4.0;  // -45 degrees
  const double s = 1.75;
  const Eigen::MatrixXd R = rot2(theta);
  Eigen::VectorXd t(2);
  t << -2.0, 5.0;
  Eigen::MatrixXd q = (s * R * p).colwise() + t;
  write_pair_csv(dir / "similarity2d_pairs.csv", p, q, w);
  write_truth(dir / "similarity2d_truth.txt",
              "scenario=similarity2d\nmode=similarity\nreflection=deny\n"
              "theta_rad=" + num(theta) +
              "\nscale=" + num(s) +
              "\nt0=" + num(t(0)) +
              "\nt1=" + num(t(1)) +
              "\nrms=0\n");
  return 0;
}

int gen_rigid3d(const fs::path& dir) {
  Eigen::MatrixXd p(3, 8);
  p << 1, -1, 2, 0, 3, -2, 1, 0,
       0,  1, 2, 3, -1, -2, 0, 1,
       1,  1, 0, 0, 2,  2, -1, 3;
  Eigen::VectorXd w(8);
  w << 1, 2, 1, 1, 3, 2, 1, 4;
  const double theta = 2.0 * std::numbers::pi / 3.0;  // 120 deg about z
  const Eigen::MatrixXd R = rot3_z(theta);
  Eigen::VectorXd t(3);
  t << 3.0, -4.0, 1.5;
  Eigen::MatrixXd q = (R * p).colwise() + t;
  write_pair_csv(dir / "rigid3d_pairs.csv", p, q, w);
  write_truth(dir / "rigid3d_truth.txt",
              "scenario=rigid3d\nmode=rigid\nreflection=deny\n"
              "theta_rad=" + num(theta) +
              "  (rotation about z)\n"
              "t0=" + num(t(0)) +
              "\nt1=" + num(t(1)) +
              "\nt2=" + num(t(2)) +
              "\nscale=1\nrms=0\n");
  return 0;
}

int gen_collinear2d(const fs::path& dir) {
  // Sources on the x-axis; +90 degree rigid rotation puts targets on the
  // y-axis. Rank 1: unique in rotation-only mode, multi-solution when
  // reflections are allowed.
  Eigen::MatrixXd p(2, 5);
  p << -2, -1, 0, 1, 2,
        0,  0, 0, 0, 0;
  Eigen::VectorXd w(5);
  w << 1, 2, 3, 2, 1;
  const Eigen::MatrixXd R = rot2(std::numbers::pi / 2.0);
  Eigen::VectorXd t(2);
  t << 4.0, -2.0;
  Eigen::MatrixXd q = (R * p).colwise() + t;
  write_pair_csv(dir / "collinear2d_pairs.csv", p, q, w);
  write_truth(dir / "collinear2d_truth.txt",
              "scenario=collinear2d\nrank=1\n"
              "rotation_only=unique (det must be +1)\n"
              "reflection_allowed=non-unique (two exact minimizers)\n"
              "t0=" + num(t(0)) +
              "\nt1=" + num(t(1)) +
              "\nscale=1\nrms=0\n");
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    std::fprintf(stderr,
                 "usage: gen_data <rigid2d|similarity2d|rigid3d|collinear2d> "
                 "<out_dir>\n");
    return 2;
  }
  const std::string scenario = argv[1];
  const fs::path dir = argv[2];
  fs::create_directories(dir);
  if (scenario == "rigid2d") return gen_rigid2d(dir);
  if (scenario == "similarity2d") return gen_similarity2d(dir);
  if (scenario == "rigid3d") return gen_rigid3d(dir);
  if (scenario == "collinear2d") return gen_collinear2d(dir);
  std::fprintf(stderr, "unknown scenario: %s\n", scenario.c_str());
  return 2;
}
