#include "polyeval/config.hpp"

#include <cctype>
#include <sstream>

namespace polyeval::config {
namespace {
std::string trim(const std::string& s) {
  std::size_t a = 0, b = s.size();
  while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
  return s.substr(a, b - a);
}
std::uint64_t u64(const std::string& v, std::size_t line, std::vector<std::string>& errs) {
  try {
    return std::stoull(trim(v));
  } catch (...) {
    errs.push_back("line " + std::to_string(line) + ": invalid integer '" + trim(v) + "'");
    return 0;
  }
}
}  // namespace

ConfigResult load_config(const std::string& text) {
  ConfigResult r;
  std::istringstream is(text);
  std::string line;
  std::size_t ln = 0;
  while (std::getline(is, line)) {
    ++ln;
    std::string l = trim(line);
    if (l.empty() || l[0] == '#') continue;
    std::size_t eq = l.find('=');
    if (eq == std::string::npos) { r.errors.push_back("line " + std::to_string(ln) + ": expected key=value"); continue; }
    std::string k = trim(l.substr(0, eq));
    std::string v = trim(l.substr(eq + 1));
    if (k == "memory_bytes") r.config.memory_bytes = u64(v, ln, r.errors);
    else if (k == "field_bytes_per_scalar") r.config.field_bytes_per_scalar = u64(v, ln, r.errors);
    else if (k == "exact_bytes_per_scalar") r.config.exact_bytes_per_scalar = u64(v, ln, r.errors);
    else if (k == "crosscheck_bits") r.config.crosscheck_bits = u64(v, ln, r.errors);
    else if (k == "exact_batch_size") r.config.exact_batch_size = u64(v, ln, r.errors);
    else if (k == "field_batch_size") r.config.field_batch_size = u64(v, ln, r.errors);
    else if (k == "version") r.config.version = v;
    else r.errors.push_back("line " + std::to_string(ln) + ": unknown key '" + k + "'");
  }
  return r;
}

}  // namespace polyeval::config
