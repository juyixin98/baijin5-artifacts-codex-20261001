// SPDX-License-Identifier: MIT
// 精确性检验：n 点 Gauss 规则对次数 <= 2n-1 的多项式精确；
// 对 2n 次单项式应有可观察的误差。参考值用闭式解析积分，不经被测内核。
#include <cmath>
#include <string>
#include <vector>

#include "gaussleg/quadrature.hpp"
#include "test_framework.hpp"

using gaussleg::makeRule;

namespace {
// ∫_a^b x^p dx 的解析值
double analyticMoment(double a, double b, int p) {
    return (std::pow(b, p + 1) - std::pow(a, p + 1)) / (p + 1);
}

double quadMonomial(const gaussleg::GaussLegendreRule& q, int p) {
    const auto& x = q.nodes();
    const auto& w = q.weights();
    double s = 0.0;
    for (int i = 0; i < q.order(); ++i)
        s += w(i) * std::pow(x(i), p);
    return s;
}
} // namespace

TEST_CASE(exactness_up_to_2n_minus_1) {
    // 覆盖到 n=16：每个 n 检验全部 0..2n-1 次单项式。
    for (int n : std::vector<int>{1, 2, 3, 4, 5, 6, 8, 11, 16}) {
        auto r = makeRule(n);
        gltest::check(r.ok(), "n=" + std::to_string(n) + " 构建成功");
        const auto& q = r.value();
        for (int p = 0; p <= 2 * n - 1; ++p) {
            double got = quadMonomial(q, p);
            double want = analyticMoment(-1.0, 1.0, p);
            // 高阶单项式数值很大，用相对容差。
            double rtol = 1e-9;
            gltest::nearRel(got, want, rtol,
                            "n=" + std::to_string(n) + " 精确积分 x^" +
                                std::to_string(p));
        }
    }
}

TEST_CASE(exactness_known_polynomials_mapped) {
    auto r = makeRule(4, 0.0, 2.0);
    gltest::check(r.ok(), "[0,2] n=4 构建成功");
    const auto& q = r.value();
    // 精确到 7 次多项式。检验若干有手算结果的积分：
    auto poly = [&](auto f) {
        const auto& x = q.nodes();
        const auto& w = q.weights();
        double s = 0.0;
        for (int i = 0; i < q.order(); ++i) s += w(i) * f(x(i));
        return s;
    };
    // ∫0^2 1 dx = 2
    gltest::nearAbs(poly([](double) { return 1.0; }), 2.0, 1e-12, "∫0^2 1");
    // ∫0^2 x dx = 2
    gltest::nearAbs(poly([](double x) { return x; }), 2.0, 1e-12, "∫0^2 x");
    // ∫0^2 x^2 dx = 8/3
    gltest::nearAbs(poly([](double x) { return x * x; }), 8.0 / 3.0, 1e-12,
                    "∫0^2 x^2");
    // ∫0^2 x^7 dx = 2^8/8 = 32
    gltest::nearAbs(poly([](double x) { return std::pow(x, 7); }), 32.0,
                    1e-9, "∫0^2 x^7");
    // 一个非平凡多项式：x^5 - 3x^2 + 1
    // ∫0^2 = 2^6/6 - 3*2^3/3 + 2 = 64/6 - 8 + 2 = 14/3
    double v = poly([](double x) {
        return x * x * x * x * x - 3.0 * x * x + 1.0;
    });
    gltest::nearRel(v, 14.0 / 3.0, 1e-10, "∫0^2 (x^5-3x^2+1) = 14/3");
}

TEST_CASE(degree_2n_is_not_exact) {
    // n 点规则对 x^{2n} 不应精确（Gauss 阶数恰好为 2n-1）。
    for (int n : std::vector<int>{2, 4, 8}) {
        auto r = makeRule(n);
        gltest::check(r.ok(), "n=" + std::to_string(n) + " 构建成功");
        double got = quadMonomial(r.value(), 2 * n);
        double want = analyticMoment(-1.0, 1.0, 2 * n); // 2/(2n+1)
        double relErr = std::abs(got - want) / std::abs(want);
        gltest::check(relErr > 1e-4,
                      "x^{" + std::to_string(2 * n) +
                          "} 应当有明显误差，证明阶数上限 2n-1 真实成立");
    }
}
