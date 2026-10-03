// Tolerance profiles loaded from simple key=value files (config/).
// No extra runtime: line-based parser, comments start with '#'.
#ifndef CHEB_CONFIG_HPP
#define CHEB_CONFIG_HPP

#include <fstream>
#include <sstream>
#include <cctype>
#include <stdexcept>
#include <string>
#include <unordered_map>

#include "cheb/errors.hpp"

namespace cheb {

struct ToleranceProfile {
  double tolerance = 1e-10;
  bool smoothness_asserted = false;
  int tail_window = 6;
  double noise_floor = 1e-13;
  double min_algebraic_p = 3.0;
};

inline std::unordered_map<std::string, std::string> read_kv(
    const std::string& path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("cannot open config file: " + path);
  std::unordered_map<std::string, std::string> kv;
  std::string line;
  while (std::getline(in, line)) {
    const auto hash = line.find('#');
    if (hash != std::string::npos) line.erase(hash);
    const auto eq = line.find('=');
    if (eq == std::string::npos) continue;
    std::string k = line.substr(0, eq);
    std::string v = line.substr(eq + 1);
    auto trim = [](std::string& s) {
      while (!s.empty() && std::isspace(static_cast<unsigned char>(s.front())))
        s.erase(s.begin());
      while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back())))
        s.pop_back();
    };
    trim(k);
    trim(v);
    if (!k.empty()) kv[k] = v;
  }
  return kv;
}

inline ToleranceProfile load_profile(const std::string& path) {
  const auto kv = read_kv(path);
  ToleranceProfile p;
  auto getd = [&](const char* k, double d) {
    auto it = kv.find(k);
    return it == kv.end() ? d : std::stod(it->second);
  };
  auto geti = [&](const char* k, int d) {
    auto it = kv.find(k);
    return it == kv.end() ? d : std::stoi(it->second);
  };
  auto getb = [&](const char* k, bool d) {
    auto it = kv.find(k);
    if (it == kv.end()) return d;
    return it->second == "true" || it->second == "1" || it->second == "yes";
  };
  p.tolerance = getd("tolerance", p.tolerance);
  p.smoothness_asserted = getb("smoothness_asserted", false);
  p.tail_window = geti("tail_window", p.tail_window);
  p.noise_floor = getd("noise_floor", p.noise_floor);
  p.min_algebraic_p = getd("min_algebraic_p", p.min_algebraic_p);
  if (!(p.tolerance > 0))
    throw std::invalid_argument("tolerance must be positive");
  return p;
}

inline AssessOptions to_assess_options(const ToleranceProfile& p) {
  AssessOptions a;
  a.tolerance = p.tolerance;
  a.smoothness_asserted = p.smoothness_asserted;
  a.tail.window = p.tail_window;
  a.tail.noise_floor = p.noise_floor;
  a.tail.min_algebraic_p = p.min_algebraic_p;
  return a;
}

}  // namespace cheb

#endif
