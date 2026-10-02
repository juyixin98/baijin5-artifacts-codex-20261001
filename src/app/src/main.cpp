// Backend entry point: parses a request file, validates the numeric contract,
// runs the product/remainder-tree kernel with memory-driven batching, and
// emits an interpretable text or JSON report.
#include "core/evaluate.h"
#include "core/memory.h"
#include "explain/report.h"
#include "explain/trace.h"
#include "numcontract/config.h"
#include "numcontract/parse.h"
#include "mp/version.h"

#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

using namespace mp;

namespace {
void usage(const char* exe) {
  std::cerr
      << "multipoint-eval " << MP_VERSION_STRING << " (" << MP_GIT_DESCRIBE << ")\n"
      << "usage: " << exe << " --request <file> [--config <file>] [--format text|json] [--steps]\n"
      << "       " << exe << " --help\n";
}

std::string read_file(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return "";
  std::ostringstream ss; ss << in.rdbuf(); return ss.str();
}
} // namespace

int main(int argc, char** argv) {
  std::string request_path, config_path;
  std::string format = "text";
  bool steps = false;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&](const char* name) -> std::string {
      if (i + 1 >= argc) { std::cerr << "missing value for " << name << "\n"; return ""; }
      return argv[++i];
    };
    if (a == "--help" || a == "-h") { usage(argv[0]); return 0; }
    else if (a == "--version") { std::cout << MP_VERSION_STRING << "\n"; return 0; }
    else if (a == "--request") { request_path = next("--request"); }
    else if (a == "--config") { config_path = next("--config"); }
    else if (a == "--format") { format = next("--format"); }
    else if (a == "--steps") { steps = true; }
    else { std::cerr << "unknown argument: " << a << "\n"; usage(argv[0]); return 2; }
  }
  if (request_path.empty()) { usage(argv[0]); return 2; }
  if (format != "text" && format != "json") {
    std::cerr << "format must be text or json\n"; return 2;
  }

  contract::Config cfg;
  if (!config_path.empty()) {
    std::string ini = read_file(config_path);
    if (ini.empty() && config_path != "-") {
      std::cerr << "cannot read config: " << config_path << "\n"; return 2;
    }
    auto errs = contract::parse_config(ini, cfg);
    if (!errs.empty()) {
      for (const auto& e : errs)
        std::cerr << "config " << contract::fail_code(e.code) << " at "
                  << e.location << ": " << e.detail << "\n";
      return 2;
    }
  }

  std::string text = read_file(request_path);
  if (text.empty()) {
    std::cerr << "cannot read request: " << request_path << "\n"; return 2;
  }

  explain::Trace trace;
  auto parsed = contract::parse_request(text);
  trace.set_identity(parsed.request.id);
  MP_TRACE(trace, "numcontract", "request.parsed",
           "jobs=" + std::to_string(parsed.request.jobs.size()) +
               " grammar_failures=" + std::to_string(parsed.failures.size()));

  explain::RequestReport report;
  report.request_id = parsed.request.id;
  report.version = MP_VERSION_STRING;
  report.request_failures = parsed.failures;
  report.trace = &trace;

  for (const auto& job : parsed.request.jobs) {
    contract::Failure vf = contract::validate_job(job, cfg);
    if (vf) {
      vf.request_id = parsed.request.id;
      explain::JobReport jr;
      jr.request_id = parsed.request.id;
      jr.job_id = job.id;
      jr.domain = contract::domain_name(job.domain);
      if (job.modulus) jr.modulus = std::to_string(*job.modulus);
      jr.point_count = job.point_tokens.size();
      jr.failure = vf;
      MP_TRACE(trace, "numcontract", "job.rejected",
               std::string(contract::fail_code(vf.code)) + " :: " + vf.detail);
      report.jobs.push_back(std::move(jr));
      continue;
    }
    MP_TRACE(trace, "numcontract", "job.accepted",
             "domain=" + std::string(contract::domain_name(job.domain)) +
                 " coeff=" + std::to_string(job.coeff_tokens.size()) +
                 " points=" + std::to_string(job.point_tokens.size()));
    report.jobs.push_back(
        core::evaluate_job(job, cfg, parsed.request.id, trace));
  }

  std::cout << (format == "json"
                    ? explain::render_json(report, steps)
                    : explain::render_text(report, steps));

  bool any_failure = !report.request_failures.empty();
  for (const auto& j : report.jobs) if (j.failure) any_failure = true;
  return any_failure ? 1 : 0;
}
