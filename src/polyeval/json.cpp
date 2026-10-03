#include "polyeval/json.hpp"

namespace polyeval::json {

std::string escape(std::string_view s) {
  std::string out;
  out.reserve(s.size() + 2);
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out.push_back(static_cast<char>(c));
        }
    }
  }
  return out;
}

std::string quote(std::string_view s) { return "\"" + escape(s) + "\""; }
std::string field(std::string_view key, std::string_view value, bool trailing) {
  return quote(key) + ":" + std::string(value) + (trailing ? "," : "");
}
std::string field_uint(std::string_view key, std::uint64_t value, bool trailing) {
  return quote(key) + ":" + std::to_string(value) + (trailing ? "," : "");
}

}  // namespace polyeval::json
