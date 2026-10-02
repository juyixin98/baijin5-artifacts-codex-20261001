#include "test_framework.hpp"
#include "pade/logging.hpp"
#include <chrono>
#include <iomanip>
#include <iostream>

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    pade::log::setEnabled(true);
    std::cout << "=== pade test suite  engine=longdouble/Eigen-JacobiSVD  cases="
              << pade_test::registry().size() << " ===" << std::endl;

    int passed = 0, failed = 0;
    std::vector<std::string> failed_names;
    const auto t0 = std::chrono::steady_clock::now();

    for (const auto& tc : pade_test::registry()) {
        std::cout << "[ RUN      ] " << tc.name << std::endl;
        try {
            tc.fn();
            ++passed;
            std::cout << "[       OK ] " << tc.name << std::endl;
        } catch (const pade_test::Failure& f) {
            ++failed;
            failed_names.push_back(tc.name);
            std::cout << "[  FAILED  ] " << tc.name << ": " << f.what() << std::endl;
        } catch (const std::exception& e) {
            ++failed;
            failed_names.push_back(tc.name);
            std::cout << "[  FAILED  ] " << tc.name << " (unexpected exception): "
                      << e.what() << std::endl;
        }
    }

    const auto dt = std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - t0).count();
    std::cout << "=== summary: " << passed << " passed, " << failed << " failed, "
              << dt << " ms ===" << std::endl;
    if (failed) {
        std::cout << "failed cases:";
        for (const auto& n : failed_names) std::cout << " " << n;
        std::cout << std::endl;
    }
    return failed ? 1 : 0;
}
