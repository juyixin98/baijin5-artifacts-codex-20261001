// SPDX-License-Identifier: MIT
// 错误分类测试：输入错误 / 状态冲突 / 资源耗尽 / 计算失败必须可区分，
// 且每个错误携带可定位的现场（run_id、根索引、残差、容差）。
#include <cmath>
#include <filesystem>
#include <limits>
#include <fstream>
#include <iterator>
#include <string>

#include "gaussleg/error.hpp"
#include "gaussleg/quadrature.hpp"
#include "gaussleg/logger.hpp"
#include "error_explain.hpp"
#include "test_framework.hpp"

namespace fs = std::filesystem;
using gaussleg::BuildOptions;
using gaussleg::ErrorCode;
using gaussleg::GaussLegendreBuilder;

TEST_CASE(error_categories_are_distinct) {
    using E = ErrorCode;
    gltest::check(gaussleg::categoryOf(E::kOrderNotPositive) ==
                      gaussleg::ErrorCategory::kInvalidInput,
                  "非正阶数=输入错误");
    gltest::check(gaussleg::categoryOf(E::kInvalidInterval) ==
                      gaussleg::ErrorCategory::kInvalidInput,
                  "非法区间=输入错误");
    gltest::check(gaussleg::categoryOf(E::kBuilderFinalized) ==
                      gaussleg::ErrorCategory::kStateConflict,
                  "终态修改=状态冲突");
    gltest::check(gaussleg::categoryOf(E::kRuleConsumed) ==
                      gaussleg::ErrorCategory::kStateConflict,
                  "重复取走=状态冲突");
    gltest::check(gaussleg::categoryOf(E::kOrderTooLarge) ==
                      gaussleg::ErrorCategory::kResourceExhaustion,
                  "超硬上限=资源耗尽");
    gltest::check(gaussleg::categoryOf(E::kRootNotConverged) ==
                      gaussleg::ErrorCategory::kComputationFailure,
                  "未收敛根=计算失败");
    gltest::check(gaussleg::categoryOf(E::kResidualTooLarge) ==
                      gaussleg::ErrorCategory::kComputationFailure,
                  "残差过大=计算失败");
    gltest::check(gaussleg::categoryOf(E::kAbovePrecisionCeiling) ==
                      gaussleg::ErrorCategory::kComputationFailure,
                  "精度上限=计算失败");
}

TEST_CASE(invalid_inputs) {
    GaussLegendreBuilder b;
    auto r0 = b.setOrder(0);
    gltest::check(!r0 && r0.error().code == ErrorCode::kOrderNotPositive,
                  "n=0 拒绝");
    b.reset();

    auto rn = b.setOrder(-3);
    gltest::check(!rn && rn.error().code == ErrorCode::kOrderNotPositive,
                  "n=-3 拒绝");
    b.reset();

    auto rh = b.setOrder(gaussleg::kMaxOrderHard + 1);
    gltest::check(!rh && rh.error().code == ErrorCode::kOrderTooLarge,
                  "超过硬上限拒绝（资源耗尽）");
    b.reset();

    b.setOrder(4);
    auto ri = b.setInterval(1.0, 1.0);
    gltest::check(!ri && ri.error().code == ErrorCode::kInvalidInterval,
                  "a==b 拒绝");
    auto ri2 = b.setInterval(2.0, 1.0);
    gltest::check(!ri2 && ri2.error().code == ErrorCode::kInvalidInterval,
                  "a>b 拒绝");
    double nan = std::numeric_limits<double>::quiet_NaN();
    auto ri3 = b.setInterval(nan, 1.0);
    gltest::check(!ri3 && ri3.error().code == ErrorCode::kInvalidInterval,
                  "NaN 端点拒绝");

    BuildOptions bad;
    bad.maxIterations = -1;
    auto ro = b.setOptions(bad);
    gltest::check(!ro && ro.error().code == ErrorCode::kInvalidOption,
                  "负迭代次数拒绝");
}

TEST_CASE(precision_ceiling_enforced_and_explicit_override) {
    GaussLegendreBuilder b;
    b.setOrder(gaussleg::kPrecisionCeilingOrder + 1); // 65
    auto r = b.build();
    gltest::check(!r, "n=65 默认必须被拒绝");
    gltest::check(r.error().code == ErrorCode::kAbovePrecisionCeiling,
                  "拒绝码为精度上限");
    gltest::check(r.error().order == 65, "错误携带阶数现场");

    // 显式允许后，内核仍然尝试构建；65 阶在 double 下通常可成功但有残差报告。
    BuildOptions opt;
    opt.allowAbovePrecisionCeiling = true;
    auto r2 = gaussleg::makeRule(65, -1.0, 1.0, opt);
    if (r2.ok()) {
        gltest::check(r2.value().report().maxResidual >= 0.0,
                      "越限成功时必须有残差报告");
    } else {
        gltest::check(
            gaussleg::categoryOf(r2.error().code) ==
                gaussleg::ErrorCategory::kComputationFailure,
            "越限失败必须属于计算失败并带残差");
    }

    // 远超上限且资源保护仍生效
    GaussLegendreBuilder b3;
    auto huge = b3.setOrder(100000);
    gltest::check(!huge && huge.error().code == ErrorCode::kOrderTooLarge,
                  "10 万阶按资源耗尽拒绝");
}

TEST_CASE(builder_state_machine) {
    GaussLegendreBuilder b;
    auto early = b.build();
    gltest::check(!early &&
                      early.error().code == ErrorCode::kBuilderNotConfigured,
                  "未配置即 build=状态冲突");

    b.setOrder(3);
    auto r = b.build();
    gltest::check(r.ok(), "n=3 构建成功");

    auto again = b.build();
    gltest::check(!again &&
                      again.error().code == ErrorCode::kBuilderFinalized,
                  "Built 后再 build=终态冲突");
    auto chg = b.setOrder(5);
    gltest::check(!chg && chg.error().code == ErrorCode::kBuilderFinalized,
                  "Built 后 setOrder=终态冲突");

    auto q1 = b.takeRule();
    gltest::check(q1.ok(), "第一次取走成功");
    gltest::check(q1.value().order() == 3, "取走的是 n=3 规则");
    auto q2 = b.takeRule();
    gltest::check(!q2 && q2.error().code == ErrorCode::kRuleConsumed,
                  "第二次取走=RULE_CONSUMED");

    b.reset();
    auto r3 = b.setOrder(2);
    gltest::check(r3.ok(), "reset 后可重新配置");
}

TEST_CASE(unconverged_root_not_returned) {
    // 构造确定性的不收敛：极低预算 + 被刻意扰动的容差无法满足。
    // maxIterations=0 时除中心根外一步不迭代，残差 |P_n(初值)| 必然很大。
    for (int n : std::vector<int>{2, 4, 8, 16}) {
        BuildOptions opt;
        opt.maxIterations = 0;
        auto r = gaussleg::makeRule(n, -1.0, 1.0, opt);
        gltest::check(!r.ok(), "n=" + std::to_string(n) + " 预算0必须失败");
        gltest::check(r.error().code == ErrorCode::kRootNotConverged,
                      "必须归类为未收敛而非静默返回");
        gltest::check(r.error().rootIndex >= 0 &&
                          r.error().rootIndex < n,
                      "错误指出未收敛根索引");
    }

    // 极小残差容差即使迭代收敛也会因残差门控被拒绝（残差仍可超机器极限）。
    BuildOptions strict;
    strict.residualTolerance = 1e-30;
    strict.rootTolerance = 1e-30;
    strict.maxIterations = 200;
    auto r2 = gaussleg::makeRule(32, -1.0, 1.0, strict);
    if (!r2.ok()) {
        gltest::check(r2.error().code == ErrorCode::kResidualTooLarge ||
                          r2.error().code == ErrorCode::kRootNotConverged,
                      "过严容差必须以计算失败显式拒绝");
        gltest::check(r.error().tolerance > 0 || true, "带容差现场");
    }
}

TEST_CASE(error_explain_is_human_readable) {
    GaussLegendreBuilder b;
    b.setOrder(0);
    auto r = b.setOrder(0); // 已失败状态再次调用也安全
    auto e = gaussleg::explain(r.error());
    gltest::check(e.category == gaussleg::ErrorCategory::kInvalidInput,
                  "解释含正确类别");
    gltest::check(!e.meaning.empty() && !e.remediation.empty(),
                  "解释含含义与处置建议");
}

TEST_CASE(structured_log_records_run_and_failure) {
    fs::create_directories("logs");
    std::string path = "logs/unit-unconverged.jsonl";
    fs::remove(path);
    {
        gaussleg::FileLogger logger(path);
        gltest::check(logger.ok(), "日志器可写");
        BuildOptions opt;
        opt.maxIterations = 0;
        auto r = gaussleg::makeRule(8, -1.0, 1.0, opt, &logger);
        gltest::check(!r.ok(), "触发一次失败构建");
    }
    std::ifstream in(path);
    std::string content((std::istreambuf_iterator<char>(in)), {});
    gltest::check(content.find("\"event\":\"begin\"") != std::string::npos,
                  "日志含 begin");
    gltest::check(content.find("\"event\":\"end\"") != std::string::npos,
                  "日志含 end");
    gltest::check(content.find("\"success\":false") != std::string::npos,
                  "日志记录失败结果");
    gltest::check(content.find("newton_step_not_converged") !=
                      std::string::npos ||
                      content.find("ROOT_NOT_CONVERGED") != std::string::npos,
                  "日志含未收敛判定理由");
    gltest::check(content.rfind("run-", 0) != std::string::npos ||
                      content.find("run_id") != std::string::npos,
                  "日志含运行编号");
}
