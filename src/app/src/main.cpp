// Command-line backend for weighted orthogonal/similarity fitting.
//
//   procrustes_fit --data <pairs.csv> [--config <profile.ini>]
//                   [--mode rigid|similarity] [--reflection deny|allow]
//                   [--rank-tol <f>] [--spread-tol <f>]
//                   [--request-id <id>] [--quiet]
//
// Exit codes: 0 success (including non-unique but usable results),
//             2 hard failure (invalid input / scale not identifiable).

#include "procrustes/config_ini.hpp"
#include "procrustes/io_csv.hpp"
#include "procrustes/logger.hpp"
#include "procrustes/procrustes.hpp"
#include "procrustes/report.hpp"

#include <chrono>
#include <iostream>
#include <random>
#include <string>

using namespace procrustes;

namespace {

void usage() {
  std::cerr << "usage: procrustes_fit --data <pairs.csv> "
               "[--config profile.ini]\n"
               "          [--mode rigid|similarity] "
               "[--reflection deny|allow]\n"
               "          [--rank-tol F] [--spread-tol F] "
               "[--request-id ID] [--quiet]\n";
}

std::string make_request_id() {
  using namespace std::chrono;
  const auto now = system_clock::now().time_since_epoch();
  const auto millis =
      duration_cast<milliseconds>(now).count();
  std::random_device rd;
  std::uniform_int_distribution<int> dist(0, 9999);
  char buf[64];
  std::snprintf(buf, sizeof(buf), "req-%lld-%04d",
                static_cast<long long>(millis), dist(rd));
  return buf;
}

}  // namespace

int main(int argc, char** argv) {
  std::string data_path, config_path, request_id;
  std::string mode_arg, refl_arg;
  bool quiet = false;
  double rank_tol = -1.0, spread_tol = -1.0;

  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto need = [&](const char* name) -> std::string {
      if (i + 1 >= argc) {
        std::cerr << "missing value for " << name << "\n";
        std::exit(2);
      }
      return argv[++i];
    };
    if (a == "--data") data_path = need("--data");
    else if (a == "--config") config_path = need("--config");
    else if (a == "--mode") mode_arg = need("--mode");
    else if (a == "--reflection") refl_arg = need("--reflection");
    else if (a == "--rank-tol") rank_tol = std::stod(need("--rank-tol"));
    else if (a == "--spread-tol") spread_tol = std::stod(need("--spread-tol"));
    else if (a == "--request-id") request_id = need("--request-id");
    else if (a == "--quiet") quiet = true;
    else if (a == "--help" || a == "-h") { usage(); return 0; }
    else {
      std::cerr << "unknown argument: " << a << "\n";
      usage();
      return 2;
    }
  }

  if (data_path.empty()) {
    usage();
    return 2;
  }

  FitConfig cfg;
  if (!config_path.empty()) {
    auto prof = config::load_ini(config_path);
    if (!prof.error.empty()) {
      std::cerr << "[config] " << prof.error << "\n";
      return 2;
    }
    cfg = prof.fit;
    if (request_id.empty() && !prof.request_id.empty())
      request_id = prof.request_id;
  }
  if (!mode_arg.empty()) {
    if (mode_arg == "rigid") cfg.estimate_scale = false;
    else if (mode_arg == "similarity") cfg.estimate_scale = true;
    else {
      std::cerr << "[config] --mode must be rigid|similarity\n";
      return 2;
    }
  }
  if (!refl_arg.empty()) {
    if (refl_arg == "deny") cfg.allow_reflection = false;
    else if (refl_arg == "allow") cfg.allow_reflection = true;
    else {
      std::cerr << "[config] --reflection must be deny|allow\n";
      return 2;
    }
  }
  if (rank_tol >= 0.0) cfg.rank_tol = rank_tol;
  if (spread_tol >= 0.0) cfg.spread_tol = spread_tol;
  if (request_id.empty()) request_id = make_request_id();

  diag::Logger logger(std::cerr, !quiet);
  RequestContext ctx{request_id};
  logger.emit(ctx, "cli", diag::Severity::Info,
              "procrustes_fit v" + std::string(kLibraryVersion) +
                  " starting; data=" + data_path +
                  " mode=" + (cfg.estimate_scale ? "similarity" : "rigid") +
                  " reflection=" + (cfg.allow_reflection ? "allow" : "deny"));

  const auto loaded = io::load_csv(data_path);
  if (!loaded.error.empty()) {
    logger.emit(ctx, "io", diag::Severity::Fail, loaded.error);
    return 2;
  }

  const FitResult r = fit(loaded.points, cfg, ctx, &logger);
  diag::ReportOptions opt;
  opt.verbose = !quiet;
  std::cout << diag::render_text(ctx, loaded.points, r, cfg, opt);

  // Independent contract verification in the CLI as well.
  if (r.status != Status::InvalidInput) {
    const auto post =
        contracts::verify_postconditions(loaded.points, r, cfg);
    if (!post.ok) {
      std::cerr << "[req=" << request_id
                << "][contract] postcondition violation:\n";
      for (const auto& v : post.violations) std::cerr << "  ! " << v << "\n";
      return 2;
    }
  }

  if (r.status == Status::InvalidInput ||
      r.status == Status::ScaleNotIdentifiable)
    return 2;
  return 0;
}
