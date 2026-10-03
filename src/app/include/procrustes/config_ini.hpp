#pragma once

// Minimal local INI-style configuration parser for run profiles.
// Recognized keys under [fit]:
//   mode = rigid | similarity
//   reflection = deny | allow
//   rank_tol = <float>
//   spread_tol = <float>
//   request_id = <string>
// Lines starting with '#' or ';' are comments.

#include "procrustes/types.hpp"

#include <map>
#include <string>

namespace procrustes::config {

struct RunProfile {
  FitConfig fit;
  std::string request_id;
  std::map<std::string, std::string> raw;
  std::string error;
};

RunProfile load_ini(const std::string& path);

}  // namespace procrustes::config
