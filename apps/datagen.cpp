// Synthetic fixture generator (no real business data).
//
// Writes paired-point CSV fixtures plus a "*.truth.txt" key/value ledger
// containing the independently hand-selected generating transform. The truth
// ledger is a fixture spec (inputs to the generator), never solver output.
#include "support/csv_io.hpp"

#include <Eigen/Dense>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using procrustes::io::writePairedCsv;

namespace {

void writeTruth(const std::string& path, const std::string& name, int d,
                int n, const std::string& mode, const Eigen::MatrixXd& R,
                double s, const Eigen::VectorXd& t, bool noisy,
                const std::string& geometry, double seed) {
  std::ofstream out(path);
  out << "name=" << name << '\n';
  out << "dimension=" << d << '\n';
  out << "point_count=" << n << '\n';
  out << "mode=" << mode << '\n';
  out << "geometry=" << geometry << '\n';
  out << "seed=" << static_cast<long long>(seed) << '\n';
  out << "noisy=" << (noisy ? "true" : "false") << '\n';
  out << std::setprecision(17);
  for (int i = 0; i < d; ++i) {
    for (int j = 0; j < d; ++j) {
      out << "R" << i << j << '=' << R(i, j) << '\n';
    }
  }
  out << "s=" << s << '\n';
  for (int i = 0; i < d; ++i) {
    out << "t" << i << '=' << t(i) << '\n';
  }
}

struct FixtureSpec {
  std::string name;
  std::string mode;
  std::string geometry;
};

}  // namespace

int main(int argc, char** argv) {
  std::string out_dir = "data";
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if ((arg == "--outdir" || arg == "-o") && i + 1 < argc) {
      out_dir = argv[++i];
    } else if (arg == "--help" || arg == "-h") {
      std::cout << "usage: datagen [--outdir data]\n";
      return 0;
    } else {
      std::cerr << "unknown argument: " << arg << '\n';
      return 2;
    }
  }
  fs::create_directories(out_dir);

  // --- fixture 1: 2D rigid (hand-picked 90 degree rotation) ----------------
  {
    Eigen::MatrixXd p(2, 4);
    p << 0, 1, 0, 1,
         0, 0, 1, 1;
    Eigen::Matrix2d R;
    R << 0, -1, 1, 0;
    Eigen::Vector2d t(2, -3);
    Eigen::MatrixXd q = (R * p).colwise() + t;
    writePairedCsv(out_dir + "/rigid2d_exact.csv", p, q,
                   Eigen::VectorXd::Ones(4));
    writeTruth(out_dir + "/rigid2d_exact.truth.txt", "rigid2d_exact", 2, 4,
               "rigid", R, 1.0, t, false, "full-rank square", 1);
  }

  // --- fixture 2: 3D similarity (hand-picked 120 degree rotation, s=2) -----
  {
    const double c = -0.5;
    const double z = std::sqrt(3.0) / 2.0;  // sin(120deg)
    Eigen::Matrix3d R;
    R << c, -z, 0, z, c, 0, 0, 0, 1;
    Eigen::Vector3d t(1, -2, 3);
    const double s = 2.0;
    Eigen::MatrixXd p(3, 5);
    p << 1, 0, 0, 1, 1,
         0, 1, 0, 1, 0,
         0, 0, 1, 0, 1;
    Eigen::MatrixXd q = (s * R * p).colwise() + t;
    writePairedCsv(out_dir + "/similarity3d_exact.csv", p, q,
                   Eigen::VectorXd::Ones(5));
    writeTruth(out_dir + "/similarity3d_exact.truth.txt",
               "similarity3d_exact", 3, 5, "similarity", R, s, t, false,
               "full-rank 3d frame", 2);
  }

  // --- fixture 3: 2D collinear, reflection-allowed gives two equal answers -
  {
    // Points lie on the x-axis; target maps x-axis to the y-axis.
    Eigen::MatrixXd p(2, 5);
    Eigen::MatrixXd q(2, 5);
    for (int i = 0; i < 5; ++i) {
      const double x = static_cast<double>(i) - 2.0;  // -2..2
      p.col(i) << x, 0.0;
      q.col(i) << 0.0, x;
    }
    Eigen::Matrix2d R;
    R << 0, -1, 1, 0;
    writePairedCsv(out_dir + "/collinear2d_reflect.csv", p, q,
                   Eigen::VectorXd::Ones(5));
    writeTruth(out_dir + "/collinear2d_reflect.truth.txt",
               "collinear2d_reflect", 2, 5, "rigid-reflect", R, 1.0,
               Eigen::Vector2d::Zero(), false, "collinear 2d", 3);
  }

  // --- fixture 4: 3D collinear (proper rotation non-unique) ----------------
  {
    Eigen::MatrixXd p(3, 5);
    Eigen::MatrixXd q(3, 5);
    for (int i = 0; i < 5; ++i) {
      const double x = static_cast<double>(i) - 2.0;
      p.col(i) << x, 0.0, 0.0;
      q.col(i) << 0.0, 0.0, x;  // x-axis to z-axis
    }
    Eigen::Matrix3d R;
    R << 0, 0, 1,
         0, 1, 0,
         1, 0, 0;
    // det(R) = -1; proper representative chosen below maps e1->e3 with det+1.
    R << 0, 0, 1,
         0, -1, 0,
         1, 0, 0;
    writePairedCsv(out_dir + "/collinear3d_proper.csv", p, q,
                   Eigen::VectorXd::Ones(5));
    writeTruth(out_dir + "/collinear3d_proper.truth.txt",
               "collinear3d_proper", 3, 5, "rigid", R, 1.0,
               Eigen::Vector3d::Zero(), false, "collinear 3d", 4);
  }

  // --- fixture 5: 2D reflection geometry -----------------------------------
  {
    // Source square mirrored across the x-axis (det = -1), translated.
    Eigen::MatrixXd p(2, 4);
    p << 0, 1, 0, 1,
         0, 0, 1, 1;
    Eigen::Matrix2d R;
    R << 1, 0, 0, -1;
    Eigen::Vector2d t(-1, 4);
    Eigen::MatrixXd q = (R * p).colwise() + t;
    writePairedCsv(out_dir + "/reflection2d_exact.csv", p, q,
                   Eigen::VectorXd::Ones(4));
    writeTruth(out_dir + "/reflection2d_exact.truth.txt",
               "reflection2d_exact", 2, 4, "rigid-reflect", R, 1.0, t, false,
               "full-rank reflected square", 5);
  }

  // --- fixture 6: weighted noisy 2D rigid, outliers down-weighted ----------
  {
    // Rigid 90-degree transform applied to centred square; independent,
    // explicitly constructed zero-mean weighted noise is added to targets.
    Eigen::MatrixXd p(2, 4);
    p << -1, 1, -1, 1,
         -1, -1, 1, 1;
    Eigen::Matrix2d R;
    R << 0, -1, 1, 0;
    Eigen::Vector2d t(3, 5);
    // Hand-set weighted-zero-mean noise vectors.
    Eigen::MatrixXd noise(2, 4);
    noise << 0.10, -0.20, 0.30, -0.20,
             0.00, 0.10, -0.20, 0.10;
    Eigen::Vector4d w(2, 1, 1, 2);
    // Project noise so sum_i w_i noise_i == 0 (keeps centroids aligned).
    Eigen::Vector2d nw = noise * w;
    for (int i = 0; i < 4; ++i) noise.col(i) -= nw / w.sum();
    Eigen::MatrixXd q = (R * p).colwise() + t + noise;
    writePairedCsv(out_dir + "/weighted_noisy2d.csv", p, q, w);
    writeTruth(out_dir + "/weighted_noisy2d.truth.txt", "weighted_noisy2d", 2,
               4, "rigid", R, 1.0, t, true, "weighted noisy square", 6);
  }

  // --- fixture 7: all source points coincide (scale unidentifiable) --------
  {
    Eigen::MatrixXd p(2, 3);
    p << 4, 4, 4,
         7, 7, 7;
    Eigen::MatrixXd q(2, 3);
    q << 1, 2, 3,
         9, 8, 7;
    Eigen::Matrix2d R;
    R << 1, 0, 0, 1;
    writePairedCsv(out_dir + "/coincident_source2d.csv", p, q,
                   Eigen::VectorXd::Ones(3));
    writeTruth(out_dir + "/coincident_source2d.truth.txt",
               "coincident_source2d", 2, 3, "similarity", R, 0.0,
               Eigen::Vector2d::Zero(), false, "coincident source", 7);
  }

  std::cout << "[datagen] wrote 7 fixtures to " << fs::absolute(out_dir)
            << '\n';
  return 0;
}
