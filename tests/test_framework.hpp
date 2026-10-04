// SPDX-License-Identifier: MIT
// 极简自测框架（无第三方测试库）：断言、失败分类与可重放日志。
#pragma once

#include <cmath>
#include <functional>
#include <iostream>
#include <string>
#include <vector>

namespace gltest {

struct Case {
    std::string name;
    std::function<void()> fn;
};

inline std::vector<Case>& registry() {
    static std::vector<Case> r;
    return r;
}

struct Registrar {
    Registrar(const std::string& name, std::function<void()> fn) {
        registry().push_back({name, std::move(fn)});
    }
};

struct AssertionError {
    std::string message;
};

inline void failNow(const std::string& msg) { throw AssertionError{msg}; }

inline void check(bool cond, const std::string& msg) {
    if (!cond) failNow(msg);
}

inline void nearAbs(double got, double want, double tol,
                    const std::string& what) {
    double err = std::abs(got - want);
    if (!(err <= tol)) {
        failNow(what + " 不满足：got=" + std::to_string(got) +
                " want=" + std::to_string(want) + " |err|=" +
                std::to_string(err) + " tol=" + std::to_string(tol));
    }
}

inline void nearRel(double got, double want, double rtol,
                    const std::string& what) {
    double scale = std::max({1.0, std::abs(got), std::abs(want)});
    double err = std::abs(got - want) / scale;
    if (!(err <= rtol)) {
        failNow(what + " 相对误差过大：got=" + std::to_string(got) +
                " want=" + std::to_string(want) + " relerr=" +
                std::to_string(err) + " rtol=" + std::to_string(rtol));
    }
}

} // namespace gltest

#define TEST_CASE(name)                                                      \
    static void name();                                                      \
    static gltest::Registrar registrar_##name(#name, name);                  \
    static void name()
