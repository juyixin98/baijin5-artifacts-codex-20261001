// SPDX-License-Identifier: MIT
// 面向用户的错误解释：含义 + 建议处置。
#pragma once

#include <string>
#include "gaussleg/error.hpp"

namespace gaussleg {
struct ExplainedError {
    ErrorCode code;
    ErrorCategory category;
    std::string codeName;
    std::string categoryName;
    std::string meaning;
    std::string remediation;
};
ExplainedError explain(const Error& err);
} // namespace gaussleg
