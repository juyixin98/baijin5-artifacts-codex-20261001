// Independent benchmark harness.
//
// Generates ONLY local synthetic polynomials/points, times the tree kernel
// against a separately implemented pointwise Horner baseline, and fits the
// observed exponents T = C * N^p with an Eigen least-squares slope over log N.
// Results are written as CSV plus an interpretable Markdown note. A fitted
// exponent that disagrees materially with the expected regime is reported as an
// uncertainty rather than silently adjusted.
#include <Eigen/Dense>
#include "core/evaluate.h"
#include "core/memory.h"
#include "explain/trace.h"
#include "numcontract/config.h"

#include <chrono>
#include <initializer_list>
#include <cstdio>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

using namespace mp;
using clk = std::chrono::steady_clock;

namespace {
double seconds_since(clk::time_point t0) {
  return std::chrono::duration<double>(clk::now() - t0).count();
}

std::string fit_slope(const std::vector<double>& log_n,
                      const std::vector<double>& log_t) {
  if (log_n.size() < 2) return "n/a";
  Eigen::MatrixXd A(log_n.size(), 2);
  Eigen::VectorXd b(log_n.size());
  for (size_t i = 0; i < log_n.size(); ++i) {
    A(i, 0) = log_n[i]; A(i, 1) = 1.0;
    b(i) = log_t[i];
  }
  Eigen::VectorXd sol = A.colPivHouseholderQr().solve(b);
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.3f", sol(0));
  return buf;
}

contract::Job make_job(const std::string& domain, uint64_t mod,
                       const std::vector<std::string>& coeff,
                       const std::vector<std::string>& pts) {
  contract::Job j;
  j.id = "bench";
  j.domain = domain == "FIELD" ? contract::Domain::Field : contract::Domain::Integer;
  if (j.domain == contract::Domain::Field) j.modulus = mod;
  j.coeff_tokens = coeff; j.has_coeff_line = true;
  j.point_tokens = pts; j.has_points_line = true;
  return j;
}

struct Row { int n; double tree_s; double horner_s; size_t batches; };
} // namespace

int main(int argc, char** argv) {
  std::string domain = "FIELD";
  uint64_t mod = 1000000007;
  std::string out_csv = "bench/bench.csv";
  std::string out_md = "bench/complexity.md";
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    if (a == "--domain" && i + 1 < argc) domain = argv[++i];
    else if (a == "--mod" && i + 1 < argc) mod = std::stoull(argv[++i]);
    else if (a == "--csv" && i + 1 < argc) out_csv = argv[++i];
    else if (a == "--md" && i + 1 < argc) out_md = argv[++i];
  }

  // Field digits stay bounded by p, so tree work scales with arithmetic
  // complexity. Exact cpp_int node coefficients grow with n, so the integer
  // sweep stops earlier to keep the benchmark quick; its note explains the
  // bit-growth cost separately from the arithmetic exponent.
  const std::vector<int> sizes = (domain == "FIELD")
                          ? std::vector<int>{64, 128, 256, 512, 1024}
                          : std::vector<int>{32, 48, 64, 96, 128};
  std::mt19937_64 rng(20240601);
  std::vector<Row> rows;
  std::vector<double> ln, lt_tree, lt_horner;

  contract::Config cfg;
  cfg.memory_limit_bytes = 256ull * 1024 * 1024;

  for (int n : sizes) {
    std::vector<std::string> coeff, pts;
    coeff.reserve(n + 1); pts.reserve(n);
    for (int i = 0; i <= n; ++i)
      coeff.push_back(std::to_string(rng() % (domain == "FIELD" ? mod : 1000)));
    for (int i = 0; i < n; ++i)
      pts.push_back(std::to_string(rng() % (domain == "FIELD" ? mod : 200000)));
    auto job = make_job(domain, mod, coeff, pts);

    explain::Trace tr;
    auto t0 = clk::now();
    auto rep = core::evaluate_job(job, cfg, "bench", tr);
    double tree = seconds_since(t0);
    if (rep.failure) {
      std::fprintf(stderr, "bench failure at n=%d: %s\n", n,
                   contract::fail_code(rep.failure.code));
      return 1;
    }

    // Separate pointwise Horner baseline (the kernel's own reference path),
    // timed independently to demonstrate the naive O(N^2) regime.
    t0 = clk::now();
    uint64_t sink = 0;
    for (const auto& x : pts) {
      std::string v = (domain == "FIELD")
                          ? core::reference::horner_field(coeff, x, mod)
                          : core::reference::horner_integer(coeff, x);
      for (char ch : v) sink += static_cast<unsigned char>(ch);
    }
    double horner = seconds_since(t0);
    if (sink == UINT64_MAX) std::fputs("", stdout);

    rows.push_back({n, tree, horner, rep.batches});
    ln.push_back(std::log(static_cast<double>(n)));
    lt_tree.push_back(std::log(tree));
    lt_horner.push_back(std::log(horner));
    std::printf("n=%5d tree=%8.4fs horner=%8.4fs batches=%zu\n",
                n, tree, horner, rep.batches);
  }

  std::string slope_tree = fit_slope(ln, lt_tree);
  std::string slope_horner = fit_slope(ln, lt_horner);

  std::ofstream csv(out_csv);
  csv << "n,tree_seconds,horner_seconds,batches,slope_tree,slope_horner,domain,mod\n";
  for (size_t i = 0; i < rows.size(); ++i)
    csv << rows[i].n << ',' << rows[i].tree_s << ',' << rows[i].horner_s << ','
        << rows[i].batches << ','
        << (i + 1 == rows.size() ? slope_tree : "") << ','
        << (i + 1 == rows.size() ? slope_horner : "") << ','
        << domain << ',' << mod << '\n';

  double st = std::stod(slope_tree), sh = std::stod(slope_horner);
  std::ofstream md(out_md);
  md << "# Complexity note (local synthetic data)\n\n"
     << "- domain: `" << domain << "` mod `"
     << (domain == "FIELD" ? std::to_string(mod) : std::string("-"))
     << "`\n"
     << "- fitted wall-clock exponent, tree kernel: **" << slope_tree
     << "**; pointwise Horner baseline: **" << slope_horner << "**\n"
     << "- arithmetic: Karatsuba multiplication + Newton series inversion gives "
        "a sub-quadratic ~O(n^1.58) polynomial-op regime for the tree; "
        "pointwise Horner is ~O(n^2).\n";
  if (domain == "INTEGER")
    md << "- exact integers: node coefficient bit width grows with n, so "
          "wall-clock exponent includes a bit-growth factor and the tree can "
          "be slower than Horner at moderate n; use FIELD for bounded digits "
          "where the contract allows it.\n";
  md << "\n"
     << "| n | tree s | Horner s | batches |\n"
     << "|---:|---:|---:|---:|\n";
  for (const auto& r : rows) {
    char b[256];
    std::snprintf(b, sizeof(b), "| %d | %.4f | %.4f | %zu |\n",
                  r.n, r.tree_s, r.horner_s, r.batches);
    md << b;
  }
  md << "\n## Interpretation\n";
  bool uncertain = false;
  if (sh < 1.6) {
    md << "- UNCERTAIN `HORNER_SLOPE_UNEXPECTED`: fitted Horner exponent "
       << slope_horner << " is below the quadratic regime; the machine may be "
          "resolution-bound at small n.\n";
    uncertain = true;
  }
  if (st >= sh - 0.05) {
    md << "- UNCERTAIN `TREE_NOT_FASTER_AT_SCALE`: tree wall exponent "
       << slope_tree << " is not clearly below Horner " << slope_horner;
    if (domain == "INTEGER")
      md << " (expected for exact integers at this scale: see bit-growth note)";
    md << ".\n";
    uncertain = true;
  }
  if (!uncertain)
    md << "- OK: tree scaling is clearly sub-quadratic relative to Horner; no uncertainty.\n";
  std::printf("fitted exponents: tree=%s horner=%s\n",
              slope_tree.c_str(), slope_horner.c_str());
  return 0;
}
