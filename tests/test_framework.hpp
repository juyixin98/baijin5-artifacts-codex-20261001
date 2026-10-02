#pragma once

#include "rlmf/error.hpp"
#include "rlmf/factorizer.hpp"
#include "rlmf/logger.hpp"

#include <cmath>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// Tiny assertion framework: each TEST(name) registers a fixture that receives
// a shared TestContext. Failures are counted by category via CHECK_* macros and
// every case writes a RunRecord (replay id + states + judgement) to the log.
struct TestContext;

namespace tctx {

struct TestCase {
    std::string name;
    std::function<void(::TestContext&)> fn;
};

inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> r;
    return r;
}

} // namespace tctx

struct TestContext {
    std::string case_name;
    rlmf::RunRecord rec;
    int failures = 0;
    int checks = 0;
    bool current_failed = false;

    void state(const std::string& key, const std::string& value) {
        rec.intermediates.emplace_back(key, value);
    }
    void judge(const std::string& why) { rec.judgement = why; }

    void fail(const std::string& expr, const std::string& detail) {
        ++failures;
        current_failed = true;
        std::cerr << "  FAIL [" << case_name << "] " << expr << " : " << detail
                  << "\n";
    }
    void check(bool cond, const std::string& expr,
               const std::string& detail = "") {
        ++checks;
        current_failed = !cond;
        if (!cond) fail(expr, detail);
    }
};

namespace tctx {
struct Registrar {
    Registrar(const std::string& name,
              std::function<void(TestContext&)> fn) {
        registry().push_back(TestCase{name, std::move(fn)});
    }
};
} // namespace tctx

#define TEST(name)                                                             \
    static void test_##name(TestContext&);                                     \
    static tctx::Registrar reg_##name(#name, test_##name);                     \
    static void test_##name(TestContext& ctx)

#define CHECK(cond) ctx.check(static_cast<bool>(cond), #cond)

#define CHECK_CLOSE(observed, expected, rel_tol, ...)                          \
    do {                                                                       \
        double _o = static_cast<double>(observed);                             \
        double _e = static_cast<double>(expected);                             \
        double _diff = std::abs(_o - _e);                                      \
        double _abstol = 0.0;                                                  \
        const char* _dummy[] = {__VA_OPT__(                                     \
            (_abstol = static_cast<double>(__VA_ARGS__), ""))};                \
        (void)_dummy;                                                          \
        double _tol = (rel_tol) * std::max(std::abs(_e), 1e-300) + _abstol;    \
        ++ctx.checks;                                                          \
        ctx.current_failed = !(_diff <= _tol + 1e-12);                         \
        if (ctx.current_failed)                                                \
            ctx.fail(#observed " ~= " #expected,                               \
                     "observed=" + std::to_string(_o) +                        \
                         " expected=" + std::to_string(_e) +                   \
                         " diff=" + std::to_string(_diff) +                    \
                         " tol=" + std::to_string(_tol));                      \
    } while (0)

#define CHECK_ERROR_KIND(result, KIND)                                         \
    do {                                                                       \
        ++ctx.checks;                                                          \
        bool _has = !(result) &&                                               \
                     (result).error().kind == rlmf::ErrorKind::KIND;           \
        ctx.current_failed = !_has;                                            \
        if (!_has)                                                             \
            ctx.fail(#result " should be " #KIND,                              \
                     (result) ? "but it succeeded"                             \
                              : std::string("got ") +                          \
                                    rlmf::error_kind_name(                     \
                                        (result).error().kind) +               \
                                    " code=" + (result).error().code);         \
    } while (0)

#define CHECK_ERROR_CODE(result, code_literal)                                 \
    do {                                                                       \
        ++ctx.checks;                                                          \
        bool _has = !(result) && (result).error().code == (code_literal);      \
        ctx.current_failed = !_has;                                            \
        if (!_has)                                                             \
            ctx.fail(#result " code " code_literal,                            \
                     (result) ? "but it succeeded"                             \
                              : "got code=" + (result).error().code);          \
    } while (0)
