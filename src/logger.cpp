// SPDX-License-Identifier: MIT
#include "gaussleg/logger.hpp"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <random>
#include <string>

namespace gaussleg {
namespace {

std::string escape(std::string_view s) {
    std::string out;
    out.reserve(s.size() + 2);
    for (char c : s) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (static_cast<unsigned char>(c) < 0x20) {
                char buf[8];
                std::snprintf(buf, sizeof(buf), "\\u%04x",
                              static_cast<unsigned char>(c));
                out += buf;
            } else {
                out += c;
            }
        }
    }
    return out;
}

std::string makeRunId() {
    using namespace std::chrono;
    auto now = system_clock::now().time_since_epoch();
    auto ns = duration_cast<nanoseconds>(now).count();
    std::mt19937_64 rng(static_cast<uint64_t>(ns));
    char buf[40];
    std::snprintf(buf, sizeof(buf), "run-%016llx-%04x",
                  static_cast<unsigned long long>(ns),
                  static_cast<unsigned>(rng() & 0xffff));
    return buf;
}

} // namespace

FileLogger::FileLogger(std::string path) : path_(std::move(path)) {
    if (path_.empty()) return;
    auto* f = std::fopen(path_.c_str(), "ae");
    if (!f) {
        path_.clear();
        return;
    }
    handle_ = f;
}

FileLogger::~FileLogger() {
    if (handle_) std::fclose(static_cast<FILE*>(handle_));
}

std::string FileLogger::beginRun(int order, double a, double b) {
    runId_ = makeRunId();
    if (!handle_) return runId_;
    auto* f = static_cast<FILE*>(handle_);
    std::fprintf(f,
                 "{\"run_id\":\"%s\",\"event\":\"begin\",\"order\":%d,"
                 "\"a\":%.17g,\"b\":%.17g}\n",
                 runId_.c_str(), order, a, b);
    std::fflush(f);
    return runId_;
}

void FileLogger::event(std::string_view phase, std::string_view key,
                       std::string_view valueJson) {
    if (!handle_) return;
    auto* f = static_cast<FILE*>(handle_);
    std::fprintf(f,
                 "{\"run_id\":\"%s\",\"event\":\"state\",\"phase\":\"%s\","
                 "\"key\":\"%s\",\"value\":%s}\n",
                 runId_.c_str(), escape(phase).c_str(), escape(key).c_str(),
                 std::string(valueJson).c_str());
    std::fflush(f);
}

void FileLogger::endRun(bool success, std::string_view reason) {
    if (!handle_) return;
    auto* f = static_cast<FILE*>(handle_);
    std::fprintf(f,
                 "{\"run_id\":\"%s\",\"event\":\"end\",\"success\":%s,"
                 "\"reason\":\"%s\"}\n",
                 runId_.c_str(), success ? "true" : "false",
                 escape(reason).c_str());
    std::fflush(f);
}

} // namespace gaussleg
