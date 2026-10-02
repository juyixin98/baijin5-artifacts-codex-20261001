#pragma once

// CSV fixtures shared by the command line tools. Format:
//   p_1,p_2[,p_3],q_1,q_2[,q_3],weight
// One point pair per row, points are row-major; dimensionality is inferred
// from the column count (2d + 1). Lines starting with '#' and blank lines are
// ignored. This parser is intentionally small and is independent of the core.
#include <Eigen/Dense>

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace procrustes::io {

struct PairedCsv {
  int dimension = 0;
  Eigen::MatrixXd source;  // d x n
  Eigen::MatrixXd target;  // d x n
  Eigen::VectorXd weights; // n
};

inline std::vector<std::string> splitCsv(const std::string& line) {
  std::vector<std::string> fields;
  std::string field;
  std::stringstream ss(line);
  while (std::getline(ss, field, ',')) {
    fields.push_back(field);
  }
  return fields;
}

inline PairedCsv readPairedCsv(const std::string& path) {
  std::ifstream in(path);
  if (!in) {
    throw std::runtime_error("cannot open CSV file: " + path);
  }
  std::vector<std::vector<double>> rows;
  std::string line;
  for (int line_no = 1; std::getline(in, line); ++line_no) {
    if (line.empty() || line[0] == '#') continue;
    auto fields = splitCsv(line);
    if (fields.size() < 3 || (fields.size() % 2) != 1) {
      throw std::runtime_error(
          "malformed CSV at line " + std::to_string(line_no) +
          ": expected 2d+1 columns, got " +
          std::to_string(fields.size()));
    }
    std::vector<double> row;
    row.reserve(fields.size());
    try {
      for (auto& f : fields) row.push_back(std::stod(f));
    } catch (const std::exception&) {
      throw std::runtime_error("non-numeric CSV field at line " +
                               std::to_string(line_no));
    }
    rows.push_back(std::move(row));
  }
  if (rows.empty()) {
    throw std::runtime_error("CSV file contained no data rows: " + path);
  }
  const int cols = static_cast<int>(rows[0].size());
  const int d = (cols - 1) / 2;
  const int n = static_cast<int>(rows.size());
  PairedCsv csv;
  csv.dimension = d;
  csv.source = Eigen::MatrixXd(d, n);
  csv.target = Eigen::MatrixXd(d, n);
  csv.weights = Eigen::VectorXd(n);
  for (int i = 0; i < n; ++i) {
    if (static_cast<int>(rows[i].size()) != cols) {
      throw std::runtime_error("ragged CSV row " + std::to_string(i + 1));
    }
    for (int k = 0; k < d; ++k) {
      csv.source(k, i) = rows[i][k];
      csv.target(k, i) = rows[i][d + k];
    }
    csv.weights(i) = rows[i][2 * d];
  }
  return csv;
}

inline void writePairedCsv(const std::string& path,
                           const Eigen::MatrixXd& source,
                           const Eigen::MatrixXd& target,
                           const Eigen::VectorXd& weights) {
  std::ofstream out(path);
  if (!out) throw std::runtime_error("cannot write CSV file: " + path);
  const int d = static_cast<int>(source.rows());
  const int n = static_cast<int>(source.cols());
  out << "# generated paired point fixture: d=" << d << " n=" << n << '\n';
  out.setf(std::ios::scientific);
  out.precision(17);
  for (int i = 0; i < n; ++i) {
    for (int k = 0; k < d; ++k) out << source(k, i) << ',';
    for (int k = 0; k < d; ++k) {
            out << target(k, i) << ",";
    }
    out << weights(i) << '\n';
  }
}

}  // namespace procrustes::io
