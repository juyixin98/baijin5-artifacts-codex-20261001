// polyeval CLI: batch polynomial evaluation via product/remainder trees.
//   polyeval eval --request <file> [--config <file>] [--log <file.jsonl>] [--explain]
//   polyeval version
// Exit codes: 0 all ok; 1 some request failed/rejected; 2 usage/IO error.
#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "polyeval/config.hpp"
#include "polyeval/contract.hpp"
#include "polyeval/driver.hpp"
#include "polyeval/explainer.hpp"
#include "polyeval/log.hpp"

namespace {

std::string slurp(const std::string& path) {
  std::ifstream f(path, std::ios::binary);
  if (!f) return "";
  std::ostringstream ss; ss << f.rdbuf();
  return ss.str();
}

void print_help() {
  std::cout <<
      "polyeval - product/remainder-tree batch polynomial evaluation\n"
      "Usage:\n"
      "  polyeval eval --request FILE [--config FILE] [--log FILE.jsonl] [--explain]\n"
      "  polyeval version\n"
      "Request grammar (one request per block, blank line separates):\n"
      "  request: <id>\n"
      "  mode: field|exact\n"
      "  prime: <p>            # field mode only, must be prime\n"
      "  coeff: c0 c1 ...      # ascending powers; F:/Z: prefixes force mode\n"
      "  points: x0 x1 ...     # duplicates retained, output keeps request order\n"
      "  memory_bytes: <n>     # optional per-request product-tree budget\n"
      "  batch_size: <n>       # optional explicit batch\n"
      "  crosscheck_bits: <n>  # exact Horner verification cap\n";
}

int cmd_eval(const std::vector<std::string>& args) {
  std::string request_path, config_path, log_path;
  bool explain = false;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    auto need = [&](const std::string& name) -> std::string {
      if (i + 1 >= args.size()) { std::cerr << "missing value for " << name << "\n"; std::exit(2); }
      return args[++i];
    };
    if (a == "--request" || a == "-r") request_path = need(a);
    else if (a == "--config" || a == "-c") config_path = need(a);
    else if (a == "--log" || a == "-l") log_path = need(a);
    else if (a == "--explain" || a == "-e") explain = true;
    else { std::cerr << "unknown argument: " << a << "\n"; return 2; }
  }
  if (request_path.empty()) { std::cerr << "--request is required\n"; return 2; }
  std::string text = slurp(request_path);
  if (text.empty() && request_path != "-") { std::cerr << "cannot read " << request_path << "\n"; return 2; }
  if (request_path == "-") { std::ostringstream ss; ss << std::cin.rdbuf(); text = ss.str(); }

  polyeval::config::EvalConfig cfg;
  if (!config_path.empty()) {
    auto cr = polyeval::config::load_config(slurp(config_path));
    cfg = cr.config;
    for (const auto& e : cr.errors) std::cerr << "config: " << e << "\n";
    if (!cr.errors.empty()) {
      std::cout << "{\"status\":\"" << polyeval::contract::fail_code_name(
                       polyeval::contract::FailCode::InvalidConfigValue)
                << "\"}\n";
      return 1;
    }
  }

  polyeval::log::JsonlLogger logger(log_path);
  auto parsed = polyeval::contract::parse_requests(text);
  auto report = polyeval::driver::run(parsed, cfg, logger);

  int rc = 0;
  // Merge failures and successful responses into request order using the
  // per-request index assigned by the parser.
  struct OutLine {
    std::size_t order;
    std::string json;
    bool failure;
    const polyeval::contract::Response* response = nullptr;
  };
  std::vector<OutLine> lines;
  for (const auto& f : report.parse_failures)
    lines.push_back({f.request_index, polyeval::driver::failure_to_json(f), true, nullptr});
  for (const auto& r : report.responses)
    lines.push_back({r.request_index, "",
                     r.status != polyeval::contract::FailCode::Ok, &r});
  std::sort(lines.begin(), lines.end(),
            [](const OutLine& a, const OutLine& b) { return a.order < b.order; });

  for (const auto& line : lines) {
    if (line.failure) {
      std::cout << line.json << "\n";
      rc = 1;
      continue;
    }
    const auto& r = *line.response;
    std::vector<polyeval::explainer::PointExplanation> ex;
    const bool want_explain = explain;
    if (want_explain) {
      ex.reserve(r.results.size());
      for (const auto& pr : r.results)
        ex.push_back(polyeval::explainer::explain_point(pr, r.mode, r.prime.value_or(0)));
    }
    std::cout << polyeval::driver::response_to_json(r, ex) << "\n";
    if (line.failure || r.status != polyeval::contract::FailCode::Ok) rc = 1;
    if (want_explain) {
      for (const auto& e : ex) {
        if (e.uncertain) std::cerr << "[UNCERTAIN] " << r.id << " #" << e.index
                                   << " x=" << e.point << " y=" << e.value
                                   << " :: " << e.interpretation << "\n";
      }
    }
  }
  return rc;
}

}  // namespace

int main(int argc, char** argv) {
  std::vector<std::string> args(argv + 1, argv + argc);
  if (args.empty() || args[0] == "-h" || args[0] == "--help") { print_help(); return 0; }
  const std::string cmd = args[0];
  std::vector<std::string> rest(args.begin() + 1, args.end());
  if (cmd == "eval") return cmd_eval(rest);
  if (cmd == "version") { std::cout << "polyeval 1.0.0 (C++20, Eigen 3.4.0, Boost 1.86.0)\n"; return 0; }
  std::cerr << "unknown command: " << cmd << "\n";
  return 2;
}
