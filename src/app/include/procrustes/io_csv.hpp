#pragma once

// Local CSV I/O for paired weighted point sets. No business data or network
// access: files are written by the bundled synthetic generator or by hand.
//
// File layout (no header), one pair per row:
//   p_1,...,p_d,q_1,...,q_d[,weight]
// Lines starting with '#' and blank lines are ignored.

#include "procrustes/types.hpp"

#include <string>

namespace procrustes::io {

struct LoadedPoints {
  PointSet points;
  std::string error;  // empty on success
};

LoadedPoints load_csv(const std::string& path);

std::string save_csv(const std::string& path, const PointSet& ps);

}  // namespace procrustes::io
