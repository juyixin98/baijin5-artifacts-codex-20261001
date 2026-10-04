// SPDX-License-Identifier: MIT
// 测试入口：顺序运行全部用例，向 logs/tests-<run>.log 写可重放记录，
// 失败时非零退出并打印 run 编号与判定理由。
#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>

#include "test_framework.hpp"

namespace fs = std::filesystem;

int main() {
    fs::create_directories("logs");
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    localtime_r(&t, &tm);
    char stamp[32];
    std::strftime(stamp, sizeof(stamp), "%Y%m%d-%H%M%S", &tm);
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                  now.time_since_epoch())
                  .count() % 100000;
    std::string runId = "test-" + std::string(stamp) + "-" +
                        std::to_string(ns);
    fs::path logPath = fs::path("logs") / (runId + ".log");
    std::ofstream log(logPath);

    int passed = 0, failed = 0;
    std::cout << "run_id=" << runId << "\n";
    log << "run_id=" << runId << " cases=" << gltest::registry().size()
        << "\n";

    for (const auto& c : gltest::registry()) {
        std::ostringstream detail;
        bool ok = true;
        std::string reason;
        try {
            c.fn();
        } catch (const gltest::AssertionError& e) {
            ok = false;
            reason = e.message;
        } catch (const std::exception& e) {
            ok = false;
            reason = std::string("异常: ") + e.what();
        }
        if (ok) {
            ++passed;
            std::cout << "[PASS] " << c.name << "\n";
            log << "[PASS] " << c.name << "\n";
        } else {
            ++failed;
            std::cout << "[FAIL] " << c.name << " :: " << reason << "\n";
            log << "[FAIL] " << c.name << " reason=" << reason << "\n";
        }
    }
    std::cout << "结果: " << passed << " 通过, " << failed
              << " 失败；日志=" << logPath.string() << "\n";
    log << "summary passed=" << passed << " failed=" << failed << "\n";
    log.close();
    return failed == 0 ? 0 : 1;
}
