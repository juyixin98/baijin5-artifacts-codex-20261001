// Command line entry point for paired-point weighted fit.
// Does no correspondence search: input rows are already paired.
#include "procrustes/explain.hpp"
#include "procrustes/fit.hpp"
#include "procrustes/logger.hpp"
#include "support/csv_io.hpp"

#include <chrono>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

struct CliArgs {
  std::string csv_path;
  std::string mode = "rigid";
  std::string request_id;
  std::string format = "text";  // text | json
  bool ok = false;
  bool show_help = false;
};

std::string makeRequestId() {
  using namespace std::chrono;
  const auto now = system_clock::now().time_since_epoch();
  const auto us = duration_cast<microseconds>(now).count();
  std::ostringstream os;
  os << "req-" << us;
  return os.str();
}

void printHelp() {
  std::cout
      << "usage: procrustes_cli --csv FILE [--mode MODE] [--request-id ID]\n"
      << "                           [--format text|json]\n\n"
      << "Modes (reflection and rotation-only are separate):\n"
      << "  rigid               orthogonal, det(R)=+1 (default)\n"
      << "  rigid-reflect       orthogonal, reflections allowed\n"
      << "  similarity          uniform scale + proper rotation\n"
      << "  similarity-reflect  uniform scale, reflections allowed\n";
}

CliArgs parseArgs(int argc, char** argv) {
  CliArgs args;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto next = [&](const char* name) -> std::string {
      if (i + 1 >= argc) {
        throw std::runtime_error(std::string("missing value for ") + name);
      }
      return argv[++i];
    };
    if (a == "--help" || a == "-h") {
      args.show_help = true;
    } else if (a == "--csv" || a == "-c") {
      args.csv_path = next("--csv");
    } else if (a == "--mode" || a == "-m") {
      args.mode = next("--mode");
    } else if (a == "--request-id") {
      args.request_id = next("--request-id");
    } else if (a == "--format" || a == "-f") {
      args.format = next("--format");
    } else {
      throw std::runtime_error("unknown argument: " + a);
    }
  }
  if (args.request_id.empty()) args.request_id = makeRequestId();
  if (!args.show_help) {
    if (args.csv_path.empty()) {
      throw std::runtime_error("--csv is required");
    }
    if (args.format != "text" && args.format != "json") {
      throw std::runtime_error("--format must be text or json");
    }
  }
  args.ok = true;
  return args;
}

}  // namespace

int main(int argc, char** argv) {
  CliArgs args;
  try {
    args = parseArgs(argc, argv);
  } catch (const std::exception& ex) {
    std::cerr << "[cli] argument error: " << ex.what() << '\n';
    printHelp();
    return 2;
  }
  if (args.show_help) {
    printHelp();
    return 0;
  }

  procrustes::Logger logger;
  logger.setRequestId(args.request_id);
  logger.log("procrustes_cli", "starting paired-point fit",
             procrustes::Severity::Info,
             "csv=" + args.csv_path + " mode=" + args.mode);

  procrustes::Request request;
  request.request_id = args.request_id;
  try {
    const auto csv = procrustes::io::readPairedCsv(args.csv_path);
    request.source = csv.source;
    request.target = csv.target;
    request.weights = csv.weights;
  } catch (const std::exception& ex) {
    logger.log("procrustes_cli", "failed to read fixture",
               procrustes::Severity::Error, ex.what());
    return 2;
  }
  if (!procrustes::parseMode(args.mode, request.mode)) {
    logger.log("procrustes_cli", "unknown mode",
               procrustes::Severity::Error, "mode=" + args.mode);
    return 2;
  }

  const procrustes::FitResult result = procrustes::fit(request);

  if (args.format == "json") {
    std::cout << procrustes::renderJson(result);
  } else {
    procrustes::writeText(result, std::cout);
  }
  logger.log("procrustes_cli",
             result.ok ? "fit complete" : "fit failed validation",
             result.ok ? procrustes::Severity::Info
                       : procrustes::Severity::Error,
             "ok=" + std::to_string(result.ok) +
                 " rotation_unique=" +
                 (result.rotation_unique ? "true" : "false"));
  return result.ok ? 0 : 1;
}
