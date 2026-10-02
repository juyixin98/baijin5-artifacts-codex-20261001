// Local fixture / report IO (simple text format; no external data sources).
#pragma once

#include <string>
#include <vector>

#include "pade/types.hpp"

namespace pade::io {

struct SeriesFile {
    std::string name;
    std::vector<Real> coeffs;
};

// Lines:  # comment / blank
//         name = <id>
//         coefficients = c0, c1, c2, ...
StatusCode loadSeriesFile(const std::string& path, SeriesFile& out);

std::string formatCoeffs(const std::vector<Real>& c, int precision = 15);

std::string renderReport(const std::string& run_id,
                         const std::string& input_name,
                         const std::string& tool_version,
                         const std::vector<Real>& coeffs,
                         const PadeResult& r,
                         bool verbose);

} // namespace pade::io
