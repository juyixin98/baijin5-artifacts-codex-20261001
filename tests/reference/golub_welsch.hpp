// SPDX-License-Identifier: MIT
// 独立参考实现：Golub-Welsch 三对角特征值法（Legendre 情形即对称 Jacobi 矩阵）。
//
// 与生产内核（Newton 迭代 + 三点递推）没有任何共享数值代码：
//   beta_k = k / sqrt(4 k^2 - 1)
// 节点为对称三对角矩阵的特征值，权重 = 2 * v_0(i)^2。
// 仅用于测试中的独立比对与夹具生成。
#pragma once

#include <Eigen/Core>
#include <Eigen/Eigenvalues>
#include <cmath>
#include <vector>

namespace glref {

struct NodesWeights {
    Eigen::VectorXd nodes;   // 升序，[-1,1]
    Eigen::VectorXd weights; // 对应权重，和为 2
};

inline NodesWeights golubWelschLegendre(int n) {
    Eigen::MatrixXd J = Eigen::MatrixXd::Zero(n, n);
    for (int k = 1; k < n; ++k) {
        double beta = static_cast<double>(k) /
                      std::sqrt(4.0 * static_cast<double>(k) * k - 1.0);
        J(k, k - 1) = beta;
        J(k - 1, k) = beta;
    }
    Eigen::SelfAdjointEigenSolver<Eigen::MatrixXd> es(J);
    NodesWeights out;
    out.nodes = es.eigenvalues(); // 已升序
    out.weights.resize(n);
    Eigen::MatrixXd V = es.eigenvectors();
    for (int i = 0; i < n; ++i) {
        out.weights(i) = 2.0 * V(0, i) * V(0, i);
    }
    return out;
}

// 独立地以 Gauss 规则计算单项式积分：∫_{-1}^1 x^p dx
inline double momentIntegral(const NodesWeights& nw, int p) {
    double s = 0.0;
    for (int i = 0; i < nw.nodes.size(); ++i) {
        s += nw.weights(i) * std::pow(nw.nodes(i), p);
    }
    return s;
}

} // namespace glref
