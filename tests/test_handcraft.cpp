// SPDX-License-Identifier: MIT
// 与手算低阶公式逐值对照（参考值独立于被测内核，来自解析公式）。
#include <numbers>

#include "gaussleg/quadrature.hpp"
#include "test_framework.hpp"

using gaussleg::makeRule;

TEST_CASE(handcraft_n1) {
    auto r = makeRule(1);
    gltest::check(r.ok(), "n=1 构建成功");
    const auto& q = r.value();
    gltest::nearAbs(q.nodes()(0), 0.0, 1e-15, "n=1 节点 0");
    gltest::nearAbs(q.weights()(0), 2.0, 1e-15, "n=1 权重 2");
}

TEST_CASE(handcraft_n2) {
    auto r = makeRule(2);
    gltest::check(r.ok(), "n=2 构建成功");
    const auto& q = r.value();
    const double s = std::numbers::inv_sqrt3; // 1/sqrt(3)
    gltest::nearAbs(q.nodes()(0), -s, 1e-15, "n=2 节点 -1/sqrt3");
    gltest::nearAbs(q.nodes()(1), s, 1e-15, "n=2 节点 +1/sqrt3");
    gltest::nearAbs(q.weights()(0), 1.0, 1e-15, "n=2 权重 1");
    gltest::nearAbs(q.weights()(1), 1.0, 1e-15, "n=2 权重 1");
}

TEST_CASE(handcraft_n3) {
    auto r = makeRule(3);
    gltest::check(r.ok(), "n=3 构建成功");
    const auto& q = r.value();
    const double s = std::sqrt(3.0 / 5.0);
    const double w = 5.0 / 9.0;
    gltest::nearAbs(q.nodes()(0), -s, 1e-15, "n=3 节点 -sqrt(3/5)");
    gltest::nearAbs(q.nodes()(1), 0.0, 1e-15, "n=3 节点 0");
    gltest::nearAbs(q.nodes()(2), s, 1e-15, "n=3 节点 +sqrt(3/5)");
    gltest::nearAbs(q.weights()(0), w, 1e-15, "n=3 端点权重 5/9");
    gltest::nearAbs(q.weights()(1), 8.0 / 9.0, 1e-15, "n=3 中心权重 8/9");
    gltest::nearAbs(q.weights()(2), w, 1e-15, "n=3 端点权重 5/9");
}

TEST_CASE(handcraft_n5_values) {
    // 教科书公认数值（独立参考）：
    // 节点 0, +/-sqrt((35-2sqrt70)/63), +/-sqrt((35+2sqrt70)/63)
    auto r = makeRule(5);
    gltest::check(r.ok(), "n=5 构建成功");
    const auto& q = r.value();
    const double x1 = std::sqrt((35.0 - 2.0 * std::sqrt(70.0)) / 63.0);
    const double x2 = std::sqrt((35.0 + 2.0 * std::sqrt(70.0)) / 63.0);
    const double w2 = (322.0 + 13.0 * std::sqrt(70.0)) / 900.0;
    const double w1 = (322.0 - 13.0 * std::sqrt(70.0)) / 900.0;
    gltest::nearAbs(q.nodes()(0), -x2, 1e-15, "n=5 最左节点");
    gltest::nearAbs(q.nodes()(1), -x1, 1e-15, "n=5 次左节点");
    gltest::nearAbs(q.nodes()(2), 0.0, 1e-15, "n=5 中心节点");
    gltest::nearAbs(q.nodes()(3), x1, 1e-15, "n=5 次右节点");
    gltest::nearAbs(q.nodes()(4), x2, 1e-15, "n=5 最右节点");
    gltest::nearAbs(q.weights()(0), w1, 1e-15, "n=5 最外权重 (322-13sqrt70)/900");
    gltest::nearAbs(q.weights()(1), w2, 1e-15, "n=5 次外权重 (322+13sqrt70)/900");
    gltest::nearAbs(q.weights()(2), 128.0 / 225.0, 1e-15, "n=5 中心权重");
    gltest::nearAbs(q.weights()(3), w2, 1e-15, "n=5 次右权重");
    gltest::nearAbs(q.weights()(4), w1, 1e-15, "n=5 最右权重");
}

TEST_CASE(mapped_interval_01) {
    // [-1,1] -> [0,1]：节点 (1+t)/2，权重 v/2；权重和=1。
    auto r = makeRule(2, 0.0, 1.0);
    gltest::check(r.ok(), "[0,1] n=2 构建成功");
    const auto& q = r.value();
    gltest::nearAbs(q.nodes()(0), (1.0 - 1.0 / std::sqrt(3.0)) / 2.0, 1e-15,
                    "[0,1] n=2 左节点");
    gltest::nearAbs(q.weights().sum(), 1.0, 1e-14, "[0,1] 权重和为 1");
    // ∫_0^1 x^3 dx = 1/4（n=2 精确，2n-1=3）
    Eigen::VectorXd fx(2);
    fx(0) = q.nodes()(0) * q.nodes()(0) * q.nodes()(0);
    fx(1) = q.nodes()(1) * q.nodes()(1) * q.nodes()(1);
    gltest::nearAbs(q.integrate(fx), 0.25, 1e-14, "∫0^1 x^3 = 1/4");
}
