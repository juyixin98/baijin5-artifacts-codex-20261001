#include "procrustes/config_ini.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>

namespace procrustes::config {
namespace {

std::string trim(const std::string& s) {
  const size_t a = s.find_first_not_of(" \t\r");
  if (a == std::string::npos) return "";
  const size_t b = s.find_last_not_of(" \t\r");
  return s.substr(a, b - a + 1);
}

std::string lower(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  return s;
}

}  // namespace

RunProfile load_ini(const std::string& path) {
  RunProfile prof;
  std::ifstream in(path);
  if (!in) {
    prof.error = "cannot open config file: " + path;
    return prof;
  }
  std::string line, section;
  size_t line_no = 0;
  while (std::getline(in, line)) {
    ++line_no;
    std::string s = trim(line);
    if (s.empty() || s[0] == '#' || s[0] == ';') continue;
    if (s.front() == '[' && s.back() == ']') {
      section = lower(s.substr(1, s.size() - 2));
      continue;
    }
    const size_t eq = s.find('=');
    if (eq == std::string::npos) {
      prof.error = "malformed config line " + std::to_string(line_no) +
                   ": " + s;
      return prof;
    }
    const std::string key = lower(trim(s.substr(0, eq)));
    const std::string val = trim(s.substr(eq + 1));
    const std::string fq = section.empty() ? key : section + "." + key;
    prof.raw[fq] = val;

    if (fq == "fit.mode") {
      if (val == "similarity") prof.fit.estimate_scale = true;
      else if (val == "rigid") prof.fit.estimate_scale = false;
      else prof.error = "unknown mode: " + val;
    } else if (fq == "fit.reflection") {
      if (val == "allow") prof.fit.allow_reflection = true;
      else if (val == "deny") prof.fit.allow_reflection = false;
      else prof.error = "reflection must be deny|allow, got: " + val;
    } else if (fq == "fit.rank_tol") {
      prof.fit.rank_tol = std::stod(val);
    } else if (fq == "fit.spread_tol") {
      prof.fit.spread_tol = std::stod(val);
    } else if (fq == "fit.request_id") {
      prof.request_id = val;
    }
    if (!prof.error.empty()) return prof;
  }
  return prof;
}

}  // namespace procrustes::config
