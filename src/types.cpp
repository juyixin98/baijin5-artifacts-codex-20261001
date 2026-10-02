#include "pade/types.hpp"

namespace pade {

std::string statusName(StatusCode code) {
    switch (code) {
    case StatusCode::kOk: return "OK";
    case StatusCode::kInvalidArgument: return "INVALID_ARGUMENT";
    case StatusCode::kInsufficientCoeffs: return "INSUFFICIENT_COEFFS";
    case StatusCode::kRankDeficient: return "RANK_DEFICIENT";
    case StatusCode::kNormalizationImpossible: return "NORMALIZATION_IMPOSSIBLE";
    case StatusCode::kPoleEvaluated: return "POLE_EVALUATED";
    case StatusCode::kIoError: return "IO_ERROR";
    case StatusCode::kInternalError: return "INTERNAL_ERROR";
    }
    return "UNKNOWN_STATUS";
}

} // namespace pade
