#pragma once
// Synthetic series sources and coefficient-file IO. No external data:
// every generator is a closed-form local fixture.
#include "pade/types.hpp"
#include <string>
#include <vector>

namespace pade::series {

enum class Kind { Exp, Sin, Cos, Geometric, Polynomial };

Kind parseKind(const std::string& name);
const char* kindName(Kind k);

std::vector<Real> generate(Kind kind, int terms,
                           const std::vector<Real>& params = {});

// One long double per line (hexfloat capable); blank/# lines allowed.
std::vector<Real> loadCoefficients(const std::string& path, std::string& error);
bool saveCoefficients(const std::string& path, const std::vector<Real>& c,
                      std::string& error);

} // namespace pade::series
