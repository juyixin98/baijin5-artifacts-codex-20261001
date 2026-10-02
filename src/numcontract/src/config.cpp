#include "numcontract/config.h"
#include <charconv>
#include <sstream>

namespace mp::contract {
namespace {
std::string trim(std::string s) {
  auto a = s.find_first_not_of(" \t\r\n");
  auto b = s.find_last_not_of(" \t\r\n");
  if (a == std::string::npos) return "";
  return s.substr(a, b - a + 1);
}
std::string lower(std::string s) {
  for (char& c : s) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
  return s;
}
} // namespace

bool parse_size_bytes(const std::string& in, uint64_t& out) noexcept {
  std::string t = trim(in);
  if (t.empty()) return false;
  size_t i = 0;
  while (i < t.size() && (isdigit(static_cast<unsigned char>(t[i])) || t[i] == ' ')) ++i;
  std::string num = trim(t.substr(0, i));
  std::string unit = lower(trim(t.substr(i)));
  uint64_t value = 0;
  auto [ptr, ec] = std::from_chars(num.data(), num.data() + num.size(), value);
  if (ec != std::errc{} || ptr != num.data() + num.size() || value == 0) return false;
  uint64_t mult = 1;
  if (unit.empty() || unit == "b" || unit == "byte" || unit == "bytes") mult = 1;
  else if (unit == "k" || unit == "kb") mult = 1000ull;
  else if (unit == "kib" || unit == "kibibytes" || unit == "ki") mult = 1024ull;
  else if (unit == "m" || unit == "mb") mult = 1000ull * 1000ull;
  else if (unit == "mib" || unit == "mibibytes" || unit == "mi") mult = 1024ull * 1024ull;
  else if (unit == "g" || unit == "gb") mult = 1000ull * 1000ull * 1000ull;
  else if (unit == "gib" || unit == "gibibytes" || unit == "gi") mult = 1024ull * 1024ull * 1024ull;
  else return false;
  if (value > UINT64_MAX / mult) return false;
  out = value * mult;
  return true;
}

std::vector<Failure> parse_config(const std::string& ini, Config& out) {
  std::vector<Failure> errs;
  std::istringstream ss(ini);
  std::string line;
  int line_no = 0;
  while (std::getline(ss, line)) {
    ++line_no;
    std::string raw = trim(line);
    if (raw.empty() || raw[0] == '#' || raw[0] == ';') continue;
    if (raw[0] == '[' && raw.back() == ']') continue; // section header
    auto eq = raw.find('=');
    if (eq == std::string::npos) {
      errs.push_back({Fail::MalformedRequest, "expected key = value",
                      "config:line " + std::to_string(line_no), ""});
      continue;
    }
    std::string key = trim(raw.substr(0, eq));
    std::string val = trim(raw.substr(eq + 1));
    auto bad = [&](const std::string& detail) {
      errs.push_back({Fail::MalformedRequest, detail,
                      "config:line " + std::to_string(line_no), ""});
    };
    if (key == "memory_limit" || key == "memory_limit_bytes") {
      uint64_t v = 0;
      if (!parse_size_bytes(val, v)) bad("invalid memory size: " + val);
      else out.memory_limit_bytes = v;
    } else if (key == "bytes_per_slot") {
      try {
        size_t pos = 0; double d = std::stod(val, &pos);
        if (trim(val.substr(pos)).empty() && d > 0) out.bytes_per_slot = d;
        else bad("bytes_per_slot must be a positive number");
      } catch (...) { bad("bytes_per_slot must be a positive number"); }
    } else if (key == "memory_fudge") {
      try {
        size_t pos = 0; double d = std::stod(val, &pos);
        if (trim(val.substr(pos)).empty() && d >= 1.0) out.memory_fudge = d;
        else bad("memory_fudge must be a number >= 1");
      } catch (...) { bad("memory_fudge must be a number >= 1"); }
    } else if (key == "max_batch_points") {
      uint64_t v = 0;
      auto [ptr, ec] = std::from_chars(val.data(), val.data() + val.size(), v);
      if (ec != std::errc{} || ptr != val.data() + val.size()) bad("max_batch_points must be an integer");
      else out.max_batch_points = v;
    } else {
      bad("unknown configuration key: " + key);
    }
  }
  return errs;
}
} // namespace mp::contract
