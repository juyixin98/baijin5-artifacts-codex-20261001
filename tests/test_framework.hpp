// Minimal standalone test framework (no third-party test dependency).
#pragma once

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

struct Stats {
    int checks = 0;
    int failures = 0;
    std::string current;
    std::vector<std::string> failure_msgs;
};

inline Stats& stats() {
    static Stats s;
    return s;
}

struct Registrar {
    Registrar(const std::string& name, std::function<void()> fn) {
        registry().push_back({name, std::move(fn)});
    }
};

inline void report(bool ok, const std::string& expr, const char* file,
                   int line, const std::string& detail = "") {
    auto& s = stats();
    ++s.checks;
    if (!ok) {
        ++s.failures;
        std::ostringstream os;
        os << file << ":" << line << " [" << s.current << "] " << expr;
        if (!detail.empty()) os << " -- " << detail;
        s.failure_msgs.push_back(os.str());
        std::cout << "    FAIL " << expr;
        if (!detail.empty()) std::cout << " (" << detail << ")";
        std::cout << "\n";
    }
}

template <class T, class U>
inline bool closeAbs(T a, U b, double tol) {
    return std::abs(double(a) - double(b)) <= tol;
}
template <class T, class U>
inline bool closeRel(T a, U b, double rtol) {
    double d = std::abs(double(a) - double(b));
    return d <= rtol * std::max({1.0, std::abs(double(a)), std::abs(double(b))});
}

int run_all(const std::string& run_id, const std::string& version);

} // namespace pade_test

#define PADE_CAT2(a, b) a##b
#define PADE_CAT(a, b) PADE_CAT2(a, b)
#define TEST_CASE(name)                                                     \
    static void PADE_CAT(PADE_TEST_FN_, __LINE__)();                        \
    namespace {                                                             \
    ::pade_test::Registrar PADE_CAT(reg_, __LINE__)(                        \
        name, &PADE_CAT(PADE_TEST_FN_, __LINE__));                          \
    }                                                                       \
    static void PADE_CAT(PADE_TEST_FN_, __LINE__)()

#define CHECK(cond)                                                          \
    ::pade_test::report(static_cast<bool>(cond), #cond, __FILE__, __LINE__)

#define CHECK_MSG(cond, detail)                                              \
    ::pade_test::report(static_cast<bool>(cond), #cond, __FILE__, __LINE__,  \
                        detail)

#define CHECK_CLOSE(a, b, tol)                                               \
    ::pade_test::report(::pade_test::closeAbs((a), (b), (tol)),             \
                        #a " ~= " #b, __FILE__, __LINE__,                    \
                        "got a=" + std::to_string(double(a)) +               \
                            " b=" + std::to_string(double(b)))

#define CHECK_CLOSE_REL(a, b, tol)                                           \
    ::pade_test::report(::pade_test::closeRel((a), (b), (tol)),             \
                        #a " ~=rel " #b, __FILE__, __LINE__,                 \
                        "got a=" + std::to_string(double(a)) +               \
                            " b=" + std::to_string(double(b)))
