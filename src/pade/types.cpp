#include "pade/types.hpp"
#include <iomanip>
#include <sstream>

namespace pade {

const char* toString(StatusCode code) noexcept {
    switch (code) {
        case StatusCode::Ok:                            return "Ok";
        case StatusCode::RankDeficient:                 return "RankDeficient";
        case StatusCode::DegenerateDenominatorConstant: return "DegenerateDenominatorConstant";
        case StatusCode::ResidualMismatch:              return "ResidualMismatch";
        case StatusCode::DenominatorNearZero:           return "DenominatorNearZero";
        case StatusCode::InvalidArgument:               return "InvalidArgument";
        case StatusCode::NumericalFailure:              return "NumericalFailure";
    }
    return "Unknown";
}

bool isFailure(StatusCode code) noexcept {
    return code == StatusCode::DegenerateDenominatorConstant ||
           code == StatusCode::InvalidArgument ||
           code == StatusCode::NumericalFailure ||
           code == StatusCode::DenominatorNearZero;
}

std::string to_string(const Vector& v) {
    std::ostringstream os;
    os << std::setprecision(9) << "[";
    for (int i = 0; i < v.size(); ++i) {
        if (i) os << ", ";
        os << static_cast<double>(v[i]);
    }
    os << "]";
    return os.str();
}

} // namespace pade
