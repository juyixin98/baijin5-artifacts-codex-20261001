#include "rlmf/logger.hpp"

#include <chrono>
#include <iomanip>
#include <memory>
#include <sstream>
#include <unistd.h>

namespace rlmf {

namespace {
std::unique_ptr<RunLogger> g_logger;
std::string timestamp() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto t = system_clock::to_time_t(now);
    std::tm tm{};
    localtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y%m%d-%H%M%S", &tm);
    return buf;
}
} // namespace

RunLogger::RunLogger(std::string log_path) : path_(std::move(log_path)) {
    out_.open(path_, std::ios::app);
    index_.open(path_ + ".index.csv", std::ios::app);
    if (index_.tellp() == 0) {
        index_ << "run_id,case,ok,error_kind,error_code,achieved_rank,"
                  "est_residual,exact_residual,svd_tail\n";
    }
}

RunLogger::~RunLogger() = default;

std::string RunLogger::new_run_id() {
    std::lock_guard<std::mutex> lk(mtx_);
    ++counter_;
    std::ostringstream os;
    os << "run-" << timestamp() << "-" << getpid() << "-"
       << std::setw(4) << std::setfill('0') << counter_;
    return os.str();
}

void RunLogger::append(const RunRecord& rec) {
    std::lock_guard<std::mutex> lk(mtx_);
    out_ << "===== " << rec.run_id << " case=" << rec.case_name
         << " =====\n";
    out_ << "matrix: " << rec.rows << " x " << rec.cols << "\n";
    out_ << "config: k=" << rec.cfg.target_rank
         << " oversampling=" << rec.cfg.oversampling
         << " power_iters=" << rec.cfg.power_iters
         << " seed=0x" << std::hex << rec.cfg.seed << std::dec
         << " rank_tol=" << rec.cfg.rank_tol << "\n";
    for (const auto& [k, v] : rec.intermediates)
        out_ << "  state[" << k << "] = " << v << "\n";
    out_ << "result: " << (rec.ok ? "OK" : "ERROR") << "\n";
    if (!rec.ok)
        out_ << "error: " << rec.error << "\n";
    const auto& d = rec.diag;
    out_ << "diag: achieved_rank=" << d.achieved_rank
         << " sketch_cols=" << d.sketch_cols
         << " ortho_rank_Q=" << d.ortho_rank_Q
         << " frob_A=" << d.frob_A
         << " est_residual=" << d.est_residual_frob
         << " exact_residual=";
    if (d.exact_residual_valid)
        out_ << d.exact_residual_frob;
    else
        out_ << "n/a";
    out_ << " svd_tail=";
    if (d.reference_valid)
        out_ << d.reference_tail_frob;
    else
        out_ << "n/a";
    out_ << "\n";
    if (!d.rationale.empty())
        out_ << "rationale: " << d.rationale << "\n";
    out_ << "judgement: " << rec.judgement << "\n\n";
    out_.flush();

    index_ << rec.run_id << ',' << rec.case_name << ','
           << (rec.ok ? 1 : 0) << ','
           << (rec.ok ? "" : error_kind_name(rec.error.kind)) << ','
           << (rec.ok ? "" : rec.error.code) << ',' << d.achieved_rank << ','
           << d.est_residual_frob << ',';
    if (d.exact_residual_valid) index_ << d.exact_residual_frob;
    index_ << ',';
    if (d.reference_valid) index_ << d.reference_tail_frob;
    index_ << '\n';
    index_.flush();
}

RunLogger& global_logger() {
    if (!g_logger)
        g_logger = std::make_unique<RunLogger>("rlmf-runs.log");
    return *g_logger;
}

void init_global_logger(const std::string& path) {
    g_logger = std::make_unique<RunLogger>(path);
}

} // namespace rlmf
