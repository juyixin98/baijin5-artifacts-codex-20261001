// SPDX-License-Identifier: MIT
// 节点对称、权重为正、权重和正确，映射区间保持不变量。
#include <cmath>
#include <string>
#include <vector>

#include "gaussleg/quadrature.hpp"
#include "test_framework.hpp"

using gaussleg::makeRule;

namespace {
void checkInvariants(const gaussleg::GaussLegendreRule& q, double a,
                     double b, double tol) {
    int n = q.order();
    const auto& x = q.nodes();
    const auto& w = q.weights();
    gltest::check(x.size() == n && w.size() == n, "节点/权重数量等于阶数");

    for (int i = 0; i < n; ++i) {
        if (i > 0)
            gltest::check(x(i) > x(i - 1), "节点严格升序");
        gltest::check(x(i) > a && x(i) < b, "节点严格位于开区间 (a,b)");
        gltest::check(w(i) > 0.0, "权重严格为正");
        gltest::check(std::isfinite(x(i)) && std::isfinite(w(i)),
                      "节点与权重均有限");
    }
    // 关于中点对称：x_i + x_{n-1-i} = a+b
    double sym = 0.0;
    for (int i = 0; i < n; ++i)
        sym = std::max(sym, std::abs(x(i) + x(n - 1 - i) - (a + b)));
    gltest::nearAbs(sym, 0.0, tol, "节点关于中点对称");
    gltest::check(sym <= tol, "对称性偏差在容差内");

    gltest::nearAbs(w.sum(), b - a, tol, "权重和等于区间长度");
}
} // namespace

TEST_CASE(invariants_canonical_orders) {
    for (int n : std::vector<int>{1, 2, 3, 4, 5, 7, 8, 13, 16, 31, 32, 64}) {
        auto r = makeRule(n);
        gltest::check(r.ok(), "规范区间 n=" + std::to_string(n) + " 构建成功");
        checkInvariants(r.value(), -1.0, 1.0, 1e-12);
    }
}

TEST_CASE(invariants_mapped_intervals) {
    struct IV {
        double a, b;
    };
    for (auto iv : std::vector<IV>{{0.0, 1.0}, {-3.0, 7.0}, {1e-3, 2.0},
                                   {-1e6, 1e6}}) {
        auto r = makeRule(12, iv.a, iv.b);
        gltest::check(r.ok(), "映射区间构建成功");
        double scale = std::max(1.0, iv.b - iv.a);
        checkInvariants(r.value(), iv.a, iv.b, 1e-10 * scale);
    }
}

TEST_CASE(invariants_report_fields) {
    auto r = makeRule(10);
    gltest::check(r.ok(), "n=10 构建成功");
    const auto& rep = r.value().report();
    gltest::check(rep.order == 10, "报告阶数为 10");
    gltest::check(rep.maxResidual >= 0.0 && rep.maxResidual <= 1e-11,
                  "报告最大残差在容差内");
    gltest::check(rep.iterationsMax >= 1 && rep.iterationsMax <= 100,
                  "报告迭代次数合理");
    gltest::check(rep.symmetryError <= 1e-12, "报告对称偏差");
    gltest::nearAbs(rep.weightSum, 2.0, 1e-12, "报告权重和");
    gltest::check(!rep.runId.empty(), "报告含 run_id");
    gltest::check(static_cast<int>(rep.traces.size()) == (10 + 1) / 2,
                  "报告包含非负半区每个根的 trace");
}

TEST_CASE(invariants_report_exposes_unconverged_root_context) {
    // 迭代预算为 0：Newton 一步都不走，非中心根必不收敛。
    gaussleg::BuildOptions opt;
    opt.maxIterations = 0;
    auto r = makeRule(8, -1.0, 1.0, opt);
    gltest::check(!r.ok(), "预算 0 时 n=8 必须失败");
    gltest::check(r.error().code == gaussleg::ErrorCode::kRootNotConverged,
                  "失败类别必须是 ROOT_NOT_CONVERGED");
    gltest::check(r.error().rootIndex >= 0, "错误须指出具体根索引");
    gltest::check(r.error().iterations == 0, "错误须记录实际迭代次数 0");
}
