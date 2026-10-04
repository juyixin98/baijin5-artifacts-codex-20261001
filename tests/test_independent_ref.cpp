// SPDX-License-Identifier: MIT
// 独立参考比对：
//  1) 与 Golub-Welsch（对称 Jacobi 矩阵特征值，独立代码路径）逐节点/权重对照；
//  2) 与由独立工具预先生成的文本夹具 data/reference/*.txt 对照；
//  3) 独立计算的 Gauss 求和与解析单项式积分对照。
// 参考答案均不由生产内核生成。
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "gaussleg/quadrature.hpp"
#include "reference/golub_welsch.hpp"
#include "test_framework.hpp"

namespace fs = std::filesystem;
using gaussleg::makeRule;

TEST_CASE(gw_node_weight_agreement) {
    for (int n : std::vector<int>{1, 2, 3, 5, 8, 13, 16, 24, 32, 48, 64}) {
        auto pr = makeRule(n);
        gltest::check(pr.ok(), "生产内核 n=" + std::to_string(n) + " 成功");
        auto ref = glref::golubWelschLegendre(n);
        const auto& q = pr.value();
        double nodeErr = 0.0, wErr = 0.0;
        for (int i = 0; i < n; ++i) {
            nodeErr = std::max(nodeErr, std::abs(q.nodes()(i) - ref.nodes(i)));
            wErr = std::max(
                wErr,
                std::abs(q.weights()(i) - ref.weights(i)));
        }
        gltest::nearAbs(nodeErr, 0.0, 1e-12,
                        "n=" + std::to_string(n) + " 节点与 GW 一致");
        gltest::nearAbs(wErr, 0.0, 1e-12,
                        "n=" + std::to_string(n) + " 权重与 GW 一致");
    }
}

TEST_CASE(gw_independent_moments) {
    // 用独立 GW 规则算积分，再与解析值对照（验证参考自身可信），
    // 同时生产内核也应达到同样精度。
    for (int n : std::vector<int>{4, 8, 16, 32}) {
        auto ref = glref::golubWelschLegendre(n);
        for (int p = 0; p <= 2 * n - 1; p += 3) {
            double want = (p % 2 == 0) ? 2.0 / (p + 1) : 0.0;
            gltest::nearRel(glref::momentIntegral(ref, p), want, 1e-9,
                            "GW 参考自身通过精确性检验 n=" +
                                std::to_string(n) + " p=" +
                                std::to_string(p));
        }
    }
}

TEST_CASE(fixture_files_n8_n64) {
    for (int n : std::vector<int>{8, 64}) {
        fs::path path =
            fs::path("data") / "reference" / ("gl_" + std::to_string(n) + ".txt");
        gltest::check(fs::exists(path),
                      "夹具文件存在: " + path.string() +
                          "（先运行 scripts/gen_fixtures.sh）");
        std::ifstream in(path);
        int fn = 0;
        in >> fn;
        gltest::check(fn == n, "夹具阶数匹配");
        std::vector<double> fnodes(n), fweights(n);
        for (int i = 0; i < n; ++i) in >> fnodes[i];
        for (int i = 0; i < n; ++i) in >> fweights[i];

        auto pr = makeRule(n);
        gltest::check(pr.ok(), "生产内核构建成功");
        const auto& q = pr.value();
        for (int i = 0; i < n; ++i) {
            gltest::nearRel(q.nodes()(i), fnodes[i], 1e-12,
                            "夹具节点对照 n=" + std::to_string(n));
            gltest::nearRel(q.weights()(i), fweights[i], 1e-12,
                            "夹具权重对照 n=" + std::to_string(n));
        }
    }
}

TEST_CASE(handwritten_fixture_n3) {
    // 手工书写的夹具（独立于任何程序），防止生成器与内核共享错误。
    fs::path path = fs::path("data") / "reference" / "gl_3_hand.txt";
    gltest::check(fs::exists(path), "手写夹具存在");
    std::ifstream in(path);
    int n = 0;
    in >> n;
    gltest::check(n == 3, "手写夹具阶数为 3");
    std::vector<double> fnodes(3), fweights(3);
    for (auto& v : fnodes) in >> v;
    for (auto& v : fweights) in >> v;
    auto pr = makeRule(3);
    gltest::check(pr.ok(), "n=3 构建");
    for (int i = 0; i < 3; ++i) {
        gltest::nearAbs(pr.value().nodes()(i), fnodes[i], 5e-15,
                        "手写节点逐值一致");
        gltest::nearAbs(pr.value().weights()(i), fweights[i], 5e-15,
                        "手写权重逐值一致");
    }
}
