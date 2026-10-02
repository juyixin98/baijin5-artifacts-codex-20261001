#pragma once
// Lightweight, dependency-free configuration layer. The file format is a line
// oriented key=value list ('#' starts a comment). Unknown keys are reported,
// malformed values are reported; nothing is silently ignored or misparsed.
#include "pade/types.hpp"
#include <map>
#include <string>
#include <vector>

namespace pade::cfg {

struct Config {
    SolveOptions solve;
    Real pole_relative_tol = Real(1e-12);
    int series_extra_terms = 8;   // coefficients generated beyond m+n+1
    std::vector<std::string> unknown_keys;
    std::vector<std::string> parse_errors;
};

// Parse from a string (the file contents). Defaults are taken from `base`.
Config parse(const std::string& text, const Config& base = {});
Config loadFile(const std::string& path, const Config& base = {});

// Serialize effective configuration (versioned, used in logs/demo).
std::string dump(const Config& c);

} // namespace pade::cfg
