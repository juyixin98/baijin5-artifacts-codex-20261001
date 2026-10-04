// SPDX-License-Identifier: MIT
// 可重放的结构化运行日志（JSON Lines）：
// 每次构建有唯一 run_id，记录关键中间状态（初值、每步残差、判定理由），
// 失败时保留出错根的现场，便于离线重放问题。
#pragma once

#include <string>
#include <string_view>

namespace gaussleg {

class RunLogger {
public:
    virtual ~RunLogger() = default;

    virtual std::string beginRun(int order, double a, double b) = 0;
    virtual void event(std::string_view phase, std::string_view key,
                       std::string_view valueJson) = 0;
    virtual void endRun(bool success, std::string_view reason) = 0;
};

// 空日志（默认）。
class NullLogger : public RunLogger {
public:
    std::string beginRun(int, double, double) override { return "null"; }
    void event(std::string_view, std::string_view, std::string_view) override {}
    void endRun(bool, std::string_view) override {}
};

// 追加写入 JSON Lines 文件的日志器。
class FileLogger : public RunLogger {
public:
    // path 为空则回退到 NullLogger 行为（ok=false）。
    explicit FileLogger(std::string path);
    ~FileLogger() override;

    bool ok() const noexcept { return !path_.empty(); }
    std::string beginRun(int order, double a, double b) override;
    void event(std::string_view phase, std::string_view key,
               std::string_view valueJson) override;
    void endRun(bool success, std::string_view reason) override;

private:
    std::string path_;
    std::string runId_;
    void* handle_{nullptr}; // FILE*，隐藏 <cstdio>
};

} // namespace gaussleg
