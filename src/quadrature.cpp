// SPDX-License-Identifier: MIT
#include "gaussleg/quadrature.hpp"

#include <algorithm>
#include <cmath>
#include <new>
#include <limits>
#include <sstream>
#include <string>

namespace gaussleg {

double legendreP(int n, double x, double* dP) {
    if (n == 0) {
        if (dP) *dP = 0.0;
        return 1.0;
    }
    double p0 = 1.0;
    double p1 = x;
    for (int k = 1; k < n; ++k) {
        double p2 = ((2.0 * k + 1.0) * x * p1 - k * p0) / (k + 1.0);
        p0 = p1;
        p1 = p2;
    }
    if (dP) {
        // P_n'(x) = n (x P_n(x) - P_{n-1}(x)) / (x^2 - 1)
        double denom = x * x - 1.0;
        if (std::isfinite(denom) && std::abs(denom) > 1e-300) {
            *dP = n * (x * p1 - p0) / denom;
        } else {
            *dP = 0.0;
        }
    }
    return p1;
}

GaussLegendreRule::GaussLegendreRule(int order, double a, double b,
                                     Eigen::VectorXd nodes,
                                     Eigen::VectorXd weights,
                                     BuildReport report)
    : order_(order), a_(a), b_(b), nodes_(std::move(nodes)),
      weights_(std::move(weights)), report_(std::move(report)) {}

double GaussLegendreRule::integrate(const Eigen::VectorXd& fx) const {
    if (fx.size() != weights_.size()) {
        return std::numeric_limits<double>::quiet_NaN();
    }
    return weights_.dot(fx);
}

} // namespace gaussleg

namespace gaussleg {
namespace {

std::string fmt(double v) {
    std::ostringstream os;
    os.precision(17);
    os << v;
    return os.str();
}

// 在 (0,1) 上求第 idx 个根（按从左到右排序时的全区间索引）。
// half 为 (n+1)/2；idx 取 0..half-1，其中 idx < n/2 为严格正根。
struct RootResult {
    double x{0};
    double residual{0};
    int iters{0};
    bool stepConverged{false};
};

// outerI 为 1..n/2 的“从最外正根向内”序号（Numerical Recipes 约定）。
RootResult findPositiveRoot(int n, int outerI, const BuildOptions& opt) {
    // 渐近初值 z = cos(pi (i - 1/4)/(n + 1/2))。
    const double angle = M_PI * (outerI - 0.25) / (n + 0.5);
    double x = std::cos(angle);

    int it = 0;
    bool stepConverged = false;
    for (; it < opt.maxIterations; ++it) {
        double dP = 0.0;
        double P = legendreP(n, x, &dP);
        if (dP == 0.0 || !std::isfinite(dP) || !std::isfinite(P)) {
            break;
        }
        double dx = P / dP;
        double xNext = x - dx;
        // 夹紧到开区间，防止偶发越界。
        if (xNext <= -1.0) xNext = -0.9999999999;
        if (xNext >= 1.0) xNext = 0.9999999999;
        x = xNext;
        ++it;
        if (std::abs(dx) < opt.rootTolerance) {
            stepConverged = true;
            break;
        }
    }
    double dP = 0.0;
    double P = legendreP(n, x, &dP);
    return RootResult{x, std::abs(P), it, stepConverged};
}

} // namespace
} // namespace gaussleg

namespace gaussleg {

GaussLegendreBuilder::GaussLegendreBuilder() = default;

void GaussLegendreBuilder::reset() {
    state_ = State::kEmpty;
    lastError_ = Error{};
    order_ = 0;
    a_ = -1.0;
    b_ = 1.0;
    orderSet_ = false;
    intervalSet_ = false;
    options_ = BuildOptions{};
    logger_ = nullptr;
    rule_.reset();
    ruleTaken_ = false;
}

Error GaussLegendreBuilder::fail(ErrorCode code, std::string msg) {
    lastError_ = Error(code, std::move(msg));
    lastError_.order = order_;
    state_ = State::kFailed;
    return lastError_;
}

Result<void> GaussLegendreBuilder::setOrder(int order) {
    if (state_ == State::kBuilt)
        return Error(ErrorCode::kBuilderFinalized,
                     "构建器已 Built，不能再修改阶数；请先 reset()。");
    if (order <= 0) {
        lastError_ = Error(ErrorCode::kOrderNotPositive,
                           "阶数必须为正整数，收到 " + std::to_string(order));
        lastError_.order = order;
        state_ = State::kFailed;
        return lastError_;
    }
    if (order > kMaxOrderHard) {
        Error e(ErrorCode::kOrderTooLarge,
                "阶数 " + std::to_string(order) + " 超过硬上限 " +
                    std::to_string(kMaxOrderHard));
        e.order = order;
        lastError_ = e;
        state_ = State::kFailed;
        return e;
    }
    order_ = order;
    orderSet_ = true;
    if (state_ == State::kFailed) state_ = State::kConfigured;
    else if (state_ == State::kEmpty) state_ = State::kConfigured;
    return {};
}

Result<void> GaussLegendreBuilder::setInterval(double a, double b) {
    if (state_ == State::kBuilt)
        return Error(ErrorCode::kBuilderFinalized,
                     "构建器已 Built，不能再修改区间；请先 reset()。");
    if (!std::isfinite(a) || !std::isfinite(b) || a >= b) {
        Error e(ErrorCode::kInvalidInterval,
                "区间非法：要求有限实数且 a < b，收到 a=" + fmt(a) +
                    ", b=" + fmt(b));
        lastError_ = e;
        state_ = State::kFailed;
        return e;
    }
    a_ = a;
    b_ = b;
    intervalSet_ = true;
    if (state_ == State::kFailed) state_ = State::kConfigured;
    else if (state_ == State::kEmpty) state_ = State::kConfigured;
    return {};
}

Result<void> GaussLegendreBuilder::setOptions(const BuildOptions& options) {
    if (state_ == State::kBuilt)
        return Error(ErrorCode::kBuilderFinalized,
                     "构建器已 Built，不能再修改选项；请先 reset()。");
    if (options.maxIterations < 0 || !std::isfinite(options.rootTolerance) ||
        !std::isfinite(options.residualTolerance) ||
        !std::isfinite(options.weightSumTolerance) ||
        options.rootTolerance <= 0.0 || options.residualTolerance <= 0.0 ||
        options.weightSumTolerance <= 0.0) {
        Error e(ErrorCode::kInvalidOption,
                "选项非法：迭代次数需 >=0，容差需为正有限值");
        lastError_ = e;
        state_ = State::kFailed;
        return e;
    }
    options_ = options;
    if (state_ == State::kEmpty) state_ = State::kConfigured;
    return {};
}

Result<void> GaussLegendreBuilder::setLogger(RunLogger* logger) {
    if (state_ == State::kBuilt)
        return Error(ErrorCode::kBuilderFinalized,
                     "构建器已 Built，不能再更换日志器；请先 reset()。");
    logger_ = logger;
    return {};
}

} // namespace gaussleg

namespace gaussleg {

Result<const GaussLegendreRule&> GaussLegendreBuilder::build() {
    if (state_ == State::kBuilt)
        return Error(ErrorCode::kBuilderFinalized,
                     "规则已构建；如需新配置请 reset()。");
    if (!orderSet_) {
        return fail(ErrorCode::kBuilderNotConfigured,
                    "尚未调用 setOrder，无法 build。");
    }
    if (order_ > kPrecisionCeilingOrder &&
        !options_.allowAbovePrecisionCeiling) {
        Error e(ErrorCode::kAbovePrecisionCeiling,
                "阶数 " + std::to_string(order_) +
                    " 高于 double 精度承诺上限 " +
                    std::to_string(kPrecisionCeilingOrder) +
                    "；如需尝试请显式允许并检查残差。");
        e.order = order_;
        lastError_ = e;
        state_ = State::kFailed;
        return e;
    }

    RunLogger& log = logger_ ? *logger_ : static_cast<RunLogger&>(nullLogger_);
    std::string runId = log.beginRun(order_, a_, b_);

    Eigen::VectorXd t;
    Eigen::VectorXd v;
    try {
        t.resize(order_);
        v.resize(order_);
    } catch (const std::bad_alloc&) {
        log.endRun(false, "allocation_failed");
        return fail(ErrorCode::kAllocationFailed, "节点/权重数组分配失败。");
    }

    BuildReport report;
    report.order = order_;
    report.a = a_;
    report.b = b_;
    report.runId = runId;
    report.traces.reserve((order_ + 1) / 2);

    const int n = order_;
    const int half = (n + 1) / 2; // 只需计算非负半区根数
    double maxResidual = 0.0;
    int maxIters = 0;

    auto reject = [&](ErrorCode code, const std::string& msg,
                      const RootTrace& tr) -> Error {
        log.event("root", "rejected",
                  "{\"index\":" + std::to_string(tr.index) +
                      ",\"residual\":" + fmt(tr.residual) +
                      ",\"iterations\":" + std::to_string(tr.iterations) +
                      ",\"reason\":\"" + tr.reason + "\"}");
        log.endRun(false, name(code));
        Error e(code, msg);
        e.order = n;
        e.rootIndex = tr.index;
        e.iterations = tr.iterations;
        e.residual = tr.residual;
        e.tolerance = options_.residualTolerance;
        lastError_ = e;
        state_ = State::kFailed;
        return e;
    };

    // 非负半区：k 从 n/2（最靠近 0）向 n-1（最靠近 1）遍历，
    // 仅计算 [0,1) 上的根，再镜像到左半区。
    double prevPositive = 0.0;
    for (int k = n / 2; k < n; ++k) {
        int j = k - n / 2; // 半区内序号 0..half-1
        bool isCenter = (n % 2 == 1) && (j == 0);

        RootResult rr;
        if (isCenter) {
            rr.x = 0.0;
            double dP0 = 0.0;
            double P0 = legendreP(n, 0.0, &dP0);
            rr.residual = std::abs(P0);
            rr.iters = 0;
            rr.stepConverged = true;
        } else {
            rr = findPositiveRoot(n, n - k, options_);
        }

        RootTrace tr;
        tr.index = k;
        tr.initialGuess =
            isCenter ? 0.0
                     : std::cos(M_PI * (n - k - 0.25) / (n + 0.5));
        tr.finalX = rr.x;
        tr.residual = rr.residual;
        tr.iterations = rr.iters;
        tr.converged = rr.stepConverged;

        log.event("root", "solved",
                  "{\"index\":" + std::to_string(k) +
                      ",\"x\":" + fmt(rr.x) +
                      ",\"residual\":" + fmt(rr.residual) +
                      ",\"iterations\":" + std::to_string(rr.iters) +
                      ",\"step_converged\":" +
                      (rr.stepConverged ? "true" : "false") + "}");

        if (!rr.stepConverged) {
            tr.accepted = false;
            tr.reason = "newton_step_not_converged";
            report.traces.push_back(tr);
            return reject(
                ErrorCode::kRootNotConverged,
                "第 " + std::to_string(k) +
                    " 个节点 Newton 迭代在 " +
                    std::to_string(options_.maxIterations) +
                    " 步内未收敛；未收敛根不能作为合法节点返回。",
                tr);
        }
        if (!isCenter && !(rr.residual <= options_.residualTolerance)) {
            tr.accepted = false;
            tr.reason = "residual_above_tolerance";
            report.traces.push_back(tr);
            return reject(
                ErrorCode::kResidualTooLarge,
                "第 " + std::to_string(k) + " 个节点残差 |P_n|=" +
                    fmt(rr.residual) + " 超过容差 " +
                    fmt(options_.residualTolerance) +
                    "；未收敛根不能作为合法节点返回。",
                tr);
        }
        // 从中心向外，根应严格递增；等价于正半区从 1 向内严格降序。
        bool orderingOk = isCenter || rr.x > prevPositive;
        if (!orderingOk) {
            tr.accepted = false;
            tr.reason = "nodes_not_strictly_ordered";
            report.traces.push_back(tr);
            return reject(ErrorCode::kSanityCheckFailed,
                          "半区根未严格有序，数值结构已损坏。", tr);
        }
        if (!isCenter) prevPositive = rr.x;
        tr.accepted = true;
        tr.reason = "accepted";
        report.traces.push_back(tr);
        maxResidual = std::max(maxResidual, rr.residual);
        maxIters = std::max(maxIters, rr.iters);

        double dP = 0.0;
        legendreP(n, rr.x, &dP);
        double w = isCenter ? 2.0 / (dP * dP)
                            : 2.0 / ((1.0 - rr.x * rr.x) * dP * dP);
        if (!(w > 0.0) || !std::isfinite(w)) {
            tr.accepted = false;
            tr.reason = "weight_not_positive";
            report.traces.push_back(tr);
            return reject(ErrorCode::kSanityCheckFailed,
                          "第 " + std::to_string(k) +
                              " 个权重非正或非有限。",
                          tr);
        }

        int right = k;
        int left = n - 1 - k;
        t[right] = rr.x;
        v[right] = w;
        if (left != right) {
            t[left] = -rr.x;
            v[left] = w;
        } else {
            t[left] = 0.0;
            v[left] = w;
        }
    }

    // ---- 映射到 [a,b] ----
    const double mid = 0.5 * (b_ + a_);
    const double halfWidth = 0.5 * (b_ - a_);
    Eigen::VectorXd nodes = halfWidth * t.array() + mid;
    Eigen::VectorXd weights = halfWidth * v.array();

    // ---- 完整性检查：对称、严格升序、正权重、权重和 ----
    double symmetryError = 0.0;
    for (int i = 0; i < n; ++i) {
        symmetryError =
            std::max(symmetryError, std::abs(nodes[i] + nodes[n - 1 - i] -
                                             (a_ + b_)));
        if (i > 0 && !(nodes[i] > nodes[i - 1])) {
            log.endRun(false, "sanity_ordering");
            return fail(ErrorCode::kSanityCheckFailed,
                        "节点未严格升序。");
        }
        if (!(weights[i] > 0.0) || !std::isfinite(weights[i]) ||
            !std::isfinite(nodes[i])) {
            log.endRun(false, "sanity_positivity");
            return fail(ErrorCode::kSanityCheckFailed,
                        "存在非正或非有限权重/节点。");
        }
    }
    double weightSum = weights.sum();
    double sumErr = std::abs(weightSum - (b_ - a_));
    if (!(sumErr <= options_.weightSumTolerance)) {
        log.event("rule", "weight_sum",
                  "{\"sum\":" + fmt(weightSum) +
                      ",\"expected\":" + fmt(b_ - a_) +
                      ",\"error\":" + fmt(sumErr) + "}");
        log.endRun(false, "sanity_weight_sum");
        Error e = fail(
            ErrorCode::kSanityCheckFailed,
            "权重和 " + fmt(weightSum) + " 与区间长度 " + fmt(b_ - a_) +
                " 偏差 " + fmt(sumErr) + " 超过容差 " +
                fmt(options_.weightSumTolerance));
        e.residual = sumErr;
        e.tolerance = options_.weightSumTolerance;
        return e;
    }

    report.iterationsMax = maxIters;
    report.maxResidual = maxResidual;
    report.weightSum = weightSum;
    report.symmetryError = symmetryError;

    log.event("rule", "accepted",
              "{\"max_residual\":" + fmt(maxResidual) +
                  ",\"max_iterations\":" + std::to_string(maxIters) +
                  ",\"weight_sum\":" + fmt(weightSum) +
                  ",\"symmetry_error\":" + fmt(symmetryError) + "}");
    log.endRun(true, "ok");

    rule_ = std::make_unique<GaussLegendreRule>(order_, a_, b_,
                                                std::move(nodes),
                                                std::move(weights),
                                                std::move(report));
    ruleTaken_ = false;
    state_ = State::kBuilt;
    return *rule_;
}

Result<GaussLegendreRule> GaussLegendreBuilder::takeRule() {
    if (state_ != State::kBuilt || !rule_) {
        if (state_ == State::kBuilt && ruleTaken_)
            return Error(ErrorCode::kRuleConsumed,
                         "规则已被 takeRule 取走，只能取一次。");
        return Error(ErrorCode::kBuilderNotConfigured,
                     "没有可取用的规则；请先成功 build()。");
    }
    if (ruleTaken_)
        return Error(ErrorCode::kRuleConsumed,
                     "规则已被 takeRule 取走，只能取一次。");
    ruleTaken_ = true;
    return std::move(*rule_);
}

Result<GaussLegendreRule> makeRule(int order, const BuildOptions& opts,
                                   RunLogger* logger) {
    GaussLegendreBuilder builder;
    auto r1 = builder.setOrder(order);
    if (!r1) return r1.error();
    builder.setOptions(opts);
    if (logger) builder.setLogger(logger);
    auto r2 = builder.build();
    if (!r2) return r2.error();
    return builder.takeRule();
}

Result<GaussLegendreRule> makeRule(int order, double a, double b,
                                   const BuildOptions& opts,
                                   RunLogger* logger) {
    GaussLegendreBuilder builder;
    auto r1 = builder.setOrder(order);
    if (!r1) return r1.error();
    auto ri = builder.setInterval(a, b);
    if (!ri) return ri.error();
    builder.setOptions(opts);
    if (logger) builder.setLogger(logger);
    auto r2 = builder.build();
    if (!r2) return r2.error();
    return builder.takeRule();
}

} // namespace gaussleg
