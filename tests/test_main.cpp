#include "test_framework.hpp"

#include "rlmf/logger.hpp"

#include <cstdlib>
#include <iostream>

int main(int argc, char** argv) {
    std::string log_path = "build/rlmf-runs.log";
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--log")
            log_path = argv[i + 1];
    }
    rlmf::init_global_logger(log_path);

    std::string only;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--filter" && i + 1 < argc)
            only = argv[i + 1];
    }

    int total_fail = 0;
    int total_checks = 0;
    int cases_run = 0;
    for (const auto& tc : tctx::registry()) {
        if (!only.empty() && tc.name.find(only) == std::string::npos)
            continue;
        TestContext ctx;
        ctx.case_name = tc.name;
        ctx.rec.run_id = rlmf::global_logger().new_run_id();
        ctx.rec.case_name = tc.name;
        std::cout << "[ RUN  ] " << tc.name << " (" << ctx.rec.run_id << ")\n";
        try {
            tc.fn(ctx);
        } catch (const std::exception& e) {
            ctx.fail("uncaught_exception", e.what());
        }
        ++cases_run;
        total_checks += ctx.checks;
        total_fail += ctx.failures;

        ctx.rec.ok = (ctx.failures == 0);
        ctx.rec.judgement =
            ctx.rec.judgement.empty()
                ? (ctx.rec.ok ? std::string("all checks passed")
                              : std::to_string(ctx.failures) +
                                    " assertion(s) failed")
                : ctx.rec.judgement;
        rlmf::global_logger().append(ctx.rec);

        std::cout << (ctx.failures ? "[ FAIL ] " : "[  OK  ] ") << tc.name
                  << " (" << ctx.checks << " checks, " << ctx.failures
                  << " failures)\n";
    }
    std::cout << "\n" << cases_run << " cases, " << total_checks
              << " checks, " << total_fail << " failures\n";
    std::cout << "replay log: " << log_path << "\n";
    return total_fail == 0 ? 0 : 1;
}
