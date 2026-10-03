#include "procrustes/io_csv.hpp"

#include <fstream>
#include <sstream>
#include <vector>

namespace procrustes::io {
namespace {

std::vector<double> split_row(const std::string& line) {
  std::vector<double> values;
  std::string field;
  std::stringstream ss(line);
  while (std::getline(ss, field, ',')) {
    const size_t a = field.find_first_not_of(" \t\r");
    const size_t b = field.find_last_not_of(" \t\r");
    if (a == std::string::npos) continue;
    values.push_back(std::stod(field.substr(a, b - a + 1)));
  }
  return values;
}

}  // namespace

LoadedPoints load_csv(const std::string& path) {
  LoadedPoints out;
  std::ifstream in(path);
  if (!in) {
    out.error = "cannot open CSV file: " + path;
    return out;
  }

  std::vector<std::vector<double>> rows;
  std::string line;
  size_t line_no = 0;
  int cols = -1;
  while (std::getline(in, line)) {
    ++line_no;
    const size_t first = line.find_first_not_of(" \t\r");
    if (first == std::string::npos || line[first] == '#') continue;
    std::vector<double> values;
    try {
      values = split_row(line);
    } catch (const std::exception& e) {
      out.error = "parse error at " + path + ":" +
                  std::to_string(line_no) + " (" + e.what() + ")";
      return out;
    }
    if (values.empty()) continue;
    if (cols < 0) cols = static_cast<int>(values.size());
    if (static_cast<int>(values.size()) != cols) {
      out.error = "column count mismatch at " + path + ":" +
                  std::to_string(line_no) + ": expected " +
                  std::to_string(cols) + " got " +
                  std::to_string(values.size());
      return out;
    }
    rows.push_back(std::move(values));
  }

  if (rows.empty()) {
    out.error = "CSV contains no data rows: " + path;
    return out;
  }
  if (cols < 2) {
    out.error =
        "need at least p,q columns (2d+1 with weights); got " +
        std::to_string(cols);
    return out;
  }

  const int n = static_cast<int>(rows.size());
  int d = 0;
  bool have_weight = false;
  if (cols % 2 == 1) {
    d = (cols - 1) / 2;
    have_weight = true;
  } else {
    if (cols % 2 != 0 || (cols / 2) < 1) {
      out.error = "column count must be 2d or 2d+1, got " +
                  std::to_string(cols);
      return out;
    }
    d = cols / 2;
  }

  out.points.p = Eigen::MatrixXd(d, n);
  out.points.q = Eigen::MatrixXd(d, n);
  out.points.w = Eigen::VectorXd(n);
  for (int i = 0; i < n; ++i) {
    for (int k = 0; k < d; ++k) {
      out.points.p(k, i) = rows[i][k];
      out.points.q(k, i) = rows[i][d + k];
    }
    out.points.w(i) = have_weight ? rows[i][2 * d] : 1.0;
  }
  return out;
}

std::string save_csv(const std::string& path, const PointSet& ps) {
  std::ofstream out(path);
  if (!out) return "cannot write CSV file: " + path;
  out << "# p_1..p_d,q_1..q_d,weight  (synthetic paired point set)\n";
  const int d = static_cast<int>(ps.p.rows());
  const int n = static_cast<int>(ps.p.cols());
  out.precision(17);
  for (int i = 0; i < n; ++i) {
    for (int k = 0; k < d; ++k) out << ps.p(k, i) << ",";
    for (int k = 0; k < d; ++k) out << ps.q(k, i) << ",";
    out << ps.w(i) << "\n";
  }
  return "";
}

}  // namespace procrustes::io
