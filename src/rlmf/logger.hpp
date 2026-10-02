#pragma once

#include "rlmf/error.hpp"
#include "rlmf/factorizer.hpp"

#include <fstream>
#include <mutex>
#include <string>
#include <vector>

namespace rlmf {

// A replay record: unique run id, request parameters, key intermediate states
// and the final judgement. Logs are plain text so a failing run can be
// reconstructed by re-running the same (run_id -> seed, shape, config) tuple.
struct RunRecord {
    std::string run_id;
    std::string case_name;
    Index rows = 0;
    Index cols = 0;
    FactorizeConfig cfg{};
    bool ok = false;
    Error error{};
    FactorizeDiagnostics diag{};
    std::vector<std::pair<std::string, std::string>> intermediates;
    std::string judgement; // why the test/run passed or failed
};

class RunLogger {
public:
    // Opens (or creates) `log_path`; also tracks a sibling .index.csv.
    explicit RunLogger(std::string log_path);
    ~RunLogger();

    RunLogger(const RunLogger&) = delete;
    RunLogger& operator=(const RunLogger&) = delete;

    std::string new_run_id();
    void append(const RunRecord& rec);
    const std::string& path() const noexcept { return path_; }

private:
    std::string path_;
    std::ofstream out_;
    std::ofstream index_;
    std::mutex mtx_;
    unsigned long counter_ = 0;
};

// Single global logger used by the test binary (initialized in test_main).
RunLogger& global_logger();
void init_global_logger(const std::string& path);

} // namespace rlmf
