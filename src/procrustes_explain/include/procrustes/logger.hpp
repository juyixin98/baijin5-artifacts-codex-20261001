#pragma once

#include "procrustes/contract.hpp"

#include "procrustes/version.hpp"

#include <iostream>
#include <ostream>
#include <source_location>
#include <string>
#include <string_view>

namespace procrustes {

// Minimal structured logger. Every line carries the request identity, the
// component/function and source location so output is traceable.
class Logger {
 public:
  explicit Logger(std::ostream& sink = std::clog) : sink_(sink) {}

  void setRequestId(std::string request_id) {
    request_id_ = std::move(request_id);
  }

  void log(std::string_view component, std::string_view message,
           Severity severity = Severity::Info,
           std::string_view detail = "",
           const std::source_location& loc =
               std::source_location::current()) const {
    sink_ << '[' << toString(severity) << ']'
          << " request_id=" << (request_id_.empty() ? "-" : request_id_)
          << " version=" << kVersionString
          << " component=" << component
          << " at=" << shortFile(loc.file_name()) << ':' << loc.line()
          << " fn=" << loc.function_name()
          << " | " << message;
    if (!detail.empty()) sink_ << " | " << detail;
    sink_ << '\n';
  }

 private:
  static std::string_view shortFile(std::string_view path) {
    const auto pos = path.find_last_of("/\\");
    return pos == std::string_view::npos ? path : path.substr(pos + 1);
  }

  std::ostream& sink_;
  std::string request_id_;
};

}  // namespace procrustes
