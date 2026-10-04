// SPDX-License-Identifier: MIT
#include "gaussleg/error.hpp"

namespace gaussleg {

ErrorCategory categoryOf(ErrorCode code) noexcept {
    switch (code) {
    case ErrorCode::kOk:
        return ErrorCategory::kNone;
    case ErrorCode::kOrderNotPositive:
    case ErrorCode::kOrderTooLarge:
    case ErrorCode::kInvalidInterval:
    case ErrorCode::kInvalidOption:
        return ErrorCategory::kInvalidInput;
    case ErrorCode::kAbovePrecisionCeiling:
    case ErrorCode::kRootNotConverged:
    case ErrorCode::kResidualTooLarge:
    case ErrorCode::kSanityCheckFailed:
        return ErrorCategory::kComputationFailure;
    case ErrorCode::kBuilderNotConfigured:
    case ErrorCode::kBuilderFinalized:
    case ErrorCode::kRuleConsumed:
        return ErrorCategory::kStateConflict;
    case ErrorCode::kAllocationFailed:
    case ErrorCode::kResourceLimit:
        return ErrorCategory::kResourceExhaustion;
    }
    return ErrorCategory::kNone;
}

const char* name(ErrorCode code) noexcept {
    switch (code) {
    case ErrorCode::kOk: return "OK";
    case ErrorCode::kOrderNotPositive: return "ORDER_NOT_POSITIVE";
    case ErrorCode::kOrderTooLarge: return "ORDER_TOO_LARGE";
    case ErrorCode::kInvalidInterval: return "INVALID_INTERVAL";
    case ErrorCode::kInvalidOption: return "INVALID_OPTION";
    case ErrorCode::kAbovePrecisionCeiling: return "ABOVE_PRECISION_CEILING";
    case ErrorCode::kBuilderNotConfigured: return "BUILDER_NOT_CONFIGURED";
    case ErrorCode::kBuilderFinalized: return "BUILDER_FINALIZED";
    case ErrorCode::kRuleConsumed: return "RULE_CONSUMED";
    case ErrorCode::kAllocationFailed: return "ALLOCATION_FAILED";
    case ErrorCode::kResourceLimit: return "RESOURCE_LIMIT";
    case ErrorCode::kRootNotConverged: return "ROOT_NOT_CONVERGED";
    case ErrorCode::kResidualTooLarge: return "RESIDUAL_TOO_LARGE";
    case ErrorCode::kSanityCheckFailed: return "SANITY_CHECK_FAILED";
    }
    return "UNKNOWN";
}

const char* name(ErrorCategory cat) noexcept {
    switch (cat) {
    case ErrorCategory::kNone: return "none";
    case ErrorCategory::kInvalidInput: return "invalid_input";
    case ErrorCategory::kStateConflict: return "state_conflict";
    case ErrorCategory::kResourceExhaustion: return "resource_exhaustion";
    case ErrorCategory::kComputationFailure: return "computation_failure";
    }
    return "unknown";
}

} // namespace gaussleg
