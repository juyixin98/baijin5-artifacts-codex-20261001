#include "pade/logging.hpp"
#include <atomic>
#include <chrono>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <unistd.h>

namespace pade::log {

namespace {
std::atomic<unsigned long> g_counter{0};
std::atomic<bool> g_enabled{true};
}

std::string makeRunId() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto t  = system_clock::to_time_t(now);
    const auto us = duration_cast<microseconds>(now.time_since_epoch()).count() % 1'000'000;
    std::tm tm{};
    localtime_r(&t, &tm);
    std::ostringstream os;
    os << std::put_time(&tm, "%Y%m%d-%H%M%S")
       << "-" << std::setw(6) << std::setfill('0') << us
       << "-p" << static_cast<long>(getpid())
       << "-r" << g_counter.fetch_add(1);
    return os.str();
}

void emit(const std::string& level, const std::string& run_id,
          const std::string& step, const std::string& msg) {
    if (!g_enabled.load()) return;
    std::ostream& out = (level == "ERROR") ? std::cerr : std::clog;
    out << "[" << level << "] run=" << run_id
        << " step=" << step << " " << msg << std::endl;
}

void info(const std::string& id, const std::string& s, const std::string& m)  { emit("INFO", id, s, m); }
void warn(const std::string& id, const std::string& s, const std::string& m)  { emit("WARN", id, s, m); }
void error(const std::string& id, const std::string& s, const std::string& m) { emit("ERROR", id, s, m); }

void setEnabled(bool e) { g_enabled.store(e); }
bool enabled() { return g_enabled.load(); }

} // namespace pade::log
