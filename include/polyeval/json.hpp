#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace polyeval::json {

std::string escape(std::string_view s);
std::string quote(std::string_view s);
std::string field(std::string_view key, std::string_view value, bool trailing = true);
std::string field_uint(std::string_view key, std::uint64_t value, bool trailing = true);

}  // namespace polyeval::json
