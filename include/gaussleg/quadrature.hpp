// SPDX-License-Identifier: MIT
// Gauss-Legendre 正交节点/权重内核的公共数值契约。
//
// 节点 x_i 与权重 w_i 定义在有限区间 [a,b] 上：
//   sum_i w_i f(x_i) 近似积分 ∫_a^b f(x) dx
// 内部先在规范区间 [-1,1] 上求根，再仿射映射：
//   x_i = (b-a)/2 * t_i + (a+b)/2,  w_i = (b-a)/2 * v_i
//
// 关键不变量（违反则拒绝返回规则）：
//   1) 节点严格升序且关于区间中点对称；
//   2) 所有权重严格为正；
//   3) 权重和等于 b-a（在容差内）；
//   4) 每个节点对应的 P_n 残差不超过残差容差——未收敛的根绝不返回。
#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include <Eigen/Core>

#include "gaussleg/config.hpp"
#include "gaussleg/error.hpp"
#include "gaussleg/logger.hpp"

namespace gaussleg {

// 单根求解的可观测中间状态（日志/报告用）。
struct RootTrace {
    int index{-1};          // 根序号（0 = 最左）
    double initialGuess{0}; // Newton 初值
    double finalX{0};       // 最终根
    double residual{0};     // |P_n(finalX)|
    int iterations{0};      // 实际迭代次数
    bool converged{false};  // 步进收敛
    bool accepted{false};   // 残差+排序检查后是否被接受
    std::string reason;     // 接受/拒绝理由
};

struct BuildReport {
    int order{0};
    double a{-1.0};
    double b{1.0};
    int iterationsMax{0};      // 所有根中最多的迭代次数
    double maxResidual{0.0};   // 规范区间上最大 |P_n(t_i)|
    double weightSum{0.0};     // 映射后权重和
    double symmetryError{0.0}; // 最大节点对称偏差
    std::string runId;
    std::vector<RootTrace> traces;
};

class GaussLegendreRule {
public:
    GaussLegendreRule() = default;
    GaussLegendreRule(int order, double a, double b,
                      Eigen::VectorXd nodes, Eigen::VectorXd weights,
                      BuildReport report);

    int order() const noexcept { return order_; }
    double lower() const noexcept { return a_; }
    double upper() const noexcept { return b_; }

    const Eigen::VectorXd& nodes() const noexcept { return nodes_; }
    const Eigen::VectorXd& weights() const noexcept { return weights_; }
    const BuildReport& report() const noexcept { return report_; }

    // 以该规则对给定采样点函数做数值积分。
    double integrate(const Eigen::VectorXd& fx) const;

    // 消费式取走节点（kRuleConsumed 状态冲突由 Builder 负责，
    // 规则本身保持只读值语义）。
private:
    int order_{0};
    double a_{-1.0};
    double b_{1.0};
    Eigen::VectorXd nodes_;
    Eigen::VectorXd weights_;
    BuildReport report_;
};

// 有显式状态机的构建器：Empty -> Configured -> Built / Failed。
class GaussLegendreBuilder {
public:
    enum class State { kEmpty, kConfigured, kBuilt, kFailed };

    GaussLegendreBuilder();

    State state() const noexcept { return state_; }
    const Error& lastError() const noexcept { return lastError_; }

    // 仅 Empty/Configured 下允许配置；Built 下修改是状态冲突。
    Result<void> setOrder(int order);
    Result<void> setInterval(double a, double b);
    Result<void> setOptions(const BuildOptions& options);
    Result<void> setLogger(RunLogger* logger); // 可为 nullptr

    // Configured 才可构建；失败进入 Failed（可用 reset 恢复）。
    Result<const GaussLegendreRule&> build();

    // Built 后取走结果，一次性；第二次取走报 kRuleConsumed。
    Result<GaussLegendreRule> takeRule();

    void reset();

private:
    State state_{State::kEmpty};
    Error lastError_{};
    int order_{0};
    double a_{-1.0};
    double b_{1.0};
    bool orderSet_{false};
    bool intervalSet_{false};
    BuildOptions options_{};
    RunLogger* logger_{nullptr};
    NullLogger nullLogger_{};
    std::unique_ptr<GaussLegendreRule> rule_{};
    bool ruleTaken_{false};

    Error fail(ErrorCode code, std::string msg);
};

// ---- 便利函数 ----

// 一次性构建 [-1,1] 规则。
Result<GaussLegendreRule> makeRule(int order, const BuildOptions& opts = {},
                                   RunLogger* logger = nullptr);

// 一次性构建 [a,b] 规则。
Result<GaussLegendreRule> makeRule(int order, double a, double b,
                                   const BuildOptions& opts = {},
                                   RunLogger* logger = nullptr);

// Legendre 多项式 P_n(x) 及其导数（三点递推，数值稳定）。
// 返回 P_n(x)，导数写入 dP。
double legendreP(int n, double x, double* dP = nullptr);

} // namespace gaussleg
