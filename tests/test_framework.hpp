#pragma once
// Minimal assertion-based test framework. Failures record the run identity of
// the involved computation and the concrete mismatch; nothing is silently ok.
#include "pade/types.hpp"
#include <cmath>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace pade_test {

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

struct Failure : std::exception {
    std::string msg;
    explicit Failure(std::string m) : msg(std::move(m)) {}
    const char* what() const noexcept override { return msg.c_str(); }
};

inline void check(bool cond, const std::string& what, const std::string& detail = "") {
    if (!cond) throw Failure(what + (detail.empty() ? "" : (" -- " + detail)));
}

inline bool approx(pade::Real a, pade::Real b, pade::Real rel = 1e-14L) {
    return std::fabs(a - b) <= rel * std::max<long double>({1, std::fabs(a), std::fabs(b)});
}

inline std::string run(const std::string& run_id, const std::string& detail) {
    return "run=" + run_id + " " + detail;
}

} // namespace pade_test

#define TEST_CASE(NAME)                                                          \
    static void NAME##_body();                                                   \
    static pade_test::Registrar NAME##_reg(#NAME, NAME##_body);                  \
    static void NAME##_body()
