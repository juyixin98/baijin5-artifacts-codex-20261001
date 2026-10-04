// SPDX-License-Identifier: MIT
#include "error_explain.hpp"

namespace gaussleg {

ExplainedError explain(const Error& err) {
    ExplainedError e;
    e.code = err.code;
    e.category = categoryOf(err.code);
    e.codeName = name(err.code);
    e.categoryName = name(e.category);

    switch (err.code) {
    case ErrorCode::kOrderNotPositive:
        e.meaning = "Gauss 规则阶数必须为正整数。";
        e.remediation = "传入 n >= 1；n 个节点精确到次数 2n-1 的多项式。";
        break;
    case ErrorCode::kOrderTooLarge:
        e.meaning = "阶数超过硬上限，出于内存与时间资源保护拒绝。";
        e.remediation = "将阶数降到 1024 以内，或在更小的问题上分批积分。";
        break;
    case ErrorCode::kInvalidInterval:
        e.meaning = "积分区间端点非有限值，或下端点不小于上端点。";
        e.remediation = "确认 a、b 为有限实数且 a < b。";
        break;
    case ErrorCode::kInvalidOption:
        e.meaning = "迭代次数或容差选项非法（NaN/负数等）。";
        e.remediation = "maxIterations >= 0，容差为正的有限实数。";
        break;
    case ErrorCode::kAbovePrecisionCeiling:
        e.meaning = "阶数高于 double 精度承诺上限 64，结果可能不可信。";
        e.remediation = "使用阶数 <= 64；如确需尝试，显式设置 "
                        "allowAbovePrecisionCeiling=true 并检查残差报告。";
        break;
    case ErrorCode::kBuilderNotConfigured:
        e.meaning = "构建器尚未配置阶数就尝试 build。";
        e.remediation = "先调用 setOrder（必要时 setInterval/setOptions）。";
        break;
    case ErrorCode::kBuilderFinalized:
        e.meaning = "构建器已产出规则，处于 Built 终态，不能再修改配置。";
        e.remediation = "调用 reset() 后重新配置，或新建一个构建器。";
        break;
    case ErrorCode::kRuleConsumed:
        e.meaning = "规则已被 takeRule 取走，只能取一次。";
        e.remediation = "重新 build，或在取走前保存副本。";
        break;
    case ErrorCode::kAllocationFailed:
        e.meaning = "节点/权重向量内存分配失败。";
        e.remediation = "降低阶数或释放内存后重试。";
        break;
    case ErrorCode::kResourceLimit:
        e.meaning = "请求超过受保护的规模或迭代资源上限。";
        e.remediation = "减小阶数/迭代预算。";
        break;
    case ErrorCode::kRootNotConverged:
        e.meaning = "Newton 迭代在预算内步进未收敛，该根不可信。";
        e.remediation = "增大 maxIterations；若仍失败则降低阶数（高阶 double "
                        "下根过于密集）。";
        break;
    case ErrorCode::kResidualTooLarge:
        e.meaning = "迭代虽停止，但 |P_n(x)| 残差仍过大，未收敛根不得作为节点。";
        e.remediation = "报告中含具体根索引与残差；增大迭代预算或降低阶数。";
        break;
    case ErrorCode::kSanityCheckFailed:
        e.meaning = "对称性、权重正性或权重和完整性检查未通过。";
        e.remediation = "属于精度耗尽信号；降低阶数到 64 以内。";
        break;
    default:
        e.meaning = "未知错误。";
        e.remediation = "查看日志中的 run_id 与现场。";
        break;
    }
    return e;
}

} // namespace gaussleg
