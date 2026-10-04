// SPDX-License-Identifier: MIT
// 数值契约：规模上限、容差与可复现的构建选项。
#pragma once

#include <cstddef>

namespace gaussleg {

// 硬上限：资源保护，任何调用都不允许超过（输入错误 kOrderTooLarge）。
inline constexpr int kMaxOrderHard = 1024;

// double 精度下承诺的最高阶。超过此阶必须显式 allowAbovePrecisionCeiling，
// 否则返回 kAbovePrecisionLimit；即使显式允许，内核仍可能以
// kResidualTooLarge / kSanityCheckFailed 拒绝不可信结果。
inline constexpr int kPrecisionCeilingOrder = 64;

// Newton-Raphson 默认参数。
inline constexpr int kDefaultMaxIterations = 100;
inline constexpr double kDefaultRootTol = 1e-14;   // |dx| 收敛阈值
inline constexpr double kDefaultResidualTol = 1e-11; // |P_n(x)| 可信阈值
inline constexpr double kDefaultWeightSumTol = 1e-10; // 映射后权重和校验

struct BuildOptions {
    int maxIterations{kDefaultMaxIterations};
    double rootTolerance{kDefaultRootTol};
    double residualTolerance{kDefaultResidualTol};
    double weightSumTolerance{kDefaultWeightSumTol};
    // false（默认）时阶数 > kPrecisionCeilingOrder 直接报精度上限错误。
    bool allowAbovePrecisionCeiling{false};
};

} // namespace gaussleg
