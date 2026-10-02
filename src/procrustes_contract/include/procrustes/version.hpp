#pragma once

// Single source of truth for the project version. Surfaced in every result
// object and structured log line so output can be traced to a build.
#define PROCUSTES_VERSION_MAJOR 1
#define PROCUSTES_VERSION_MINOR 0
#define PROCUSTES_VERSION_PATCH 0
#define PROCUSTES_VERSION_STRING "1.0.0"

namespace procrustes {

inline constexpr const char* kVersionString = PROCUSTES_VERSION_STRING;

}  // namespace procrustes
