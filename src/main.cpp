// pade-cli: runnable service entry.
//   solve --file <series.txt> --m M --n N [--run-id ID] [--tol T]
//   eval  --file <series.txt> --m M --n N --x X
//   serve [--run-id ID]          line protocol on stdin:
//                                SOLVE <path> <m> <n>
//                                EVAL  <path> <m> <n> <x>
//                                QUIT
// Exit codes: 0 ok / 2 invalid input / 3 insufficient coeffs /
//             10 rank deficient (diagnostic) / 11 normalization impossible /
//             20 pole evaluated (eval only) / 1 other failure
#include <chrono>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "pade/io.hpp"
#include "pade/solver.hpp"

#ifndef PADE_VERSION
#define PADE_VERSION "0.0.0-dev"
#endif

namespace {

struct Args {
    std::string file, run_id;
    int m = -1, n = -1;
    double x = 0.0, tol = 0.0;
    bool verbose = false;
};

bool readArgs(int argc, char** argv, Args& a, std::string& err) {
    for (int i = 0; i < argc; ++i) {
        std::string k = argv[i];
        auto need = [&]() -> std::string {
            if (i + 1 >= argc) { err = "missing value for " + k; return ""; }
            return argv[++i];
        };
        if (k == "--file") a.file = need();
        else if (k == "--m") a.m = std::stoi(need());
        else if (k == "--n") a.n = std::stoi(need());
        else if (k == "--x") a.x = std::stod(need());
        else if (k == "--tol") a.tol = std::stod(need());
        else if (k == "--run-id") a.run_id = need();
        else if (k == "--verbose") a.verbose = true;
        else if (k == "--help" || k == "-h") { err = "__help__"; return false; }
        else if (!k.empty() && k[0] == '-') { err = "unknown flag " + k; return false; }
    }
    return true;
}

int exitFor(pade::StatusCode s) {
    using namespace pade;
    switch (s) {
    case StatusCode::kOk: return 0;
    case StatusCode::kInvalidArgument:
    case StatusCode::kIoError: return 2;
    case StatusCode::kInsufficientCoeffs: return 3;
    case StatusCode::kRankDeficient: return 10;
    case StatusCode::kNormalizationImpossible: return 11;
    case StatusCode::kPoleEvaluated: return 20;
    default: return 1;
    }
}

std::string makeRunId() {
    using namespace std::chrono;
    auto ms = duration_cast<milliseconds>(
                  system_clock::now().time_since_epoch())
                  .count();
    std::ostringstream os;
    os << "run-" << ms;
    return os.str();
}

pade::PadeResult loadAndSolve(const std::string& path, int m, int n, double tol,
                              pade::io::SeriesFile& sf,
                              pade::StatusCode& loadStatus) {
    loadStatus = pade::io::loadSeriesFile(path, sf);
    pade::Options opts;
    opts.numerator_order = m;
    opts.denominator_order = n;
    if (tol > 0.0) opts.singular_tol = tol;
    if (loadStatus != pade::StatusCode::kOk) {
        pade::PadeResult r;
        r.status = loadStatus;
        r.message = "failed to load series file: " + path;
        r.requested_m = m; r.requested_n = n;
        return r;
    }
    return pade::padeApproximate(sf.coeffs, opts);
}

void printHelp() {
    std::cout
        << "pade-cli " << PADE_VERSION << "\n"
        << "usage:\n"
        << "  solve --file <series.txt> --m M --n N [--run-id ID] [--tol T]\n"
        << "  eval  --file <series.txt> --m M --n N --x X [--run-id ID]\n"
        << "  serve [--run-id ID]\n"
        << "series file lines: name = <id> / coefficients = c0,c1,...\n";
}

} // namespace

int main(int argc, char** argv) {
    if (argc < 2) { printHelp(); return 2; }
    std::string cmd = argv[1];
    if (cmd == "--help" || cmd == "-h") { printHelp(); return 0; }

    Args a;
    std::string err;
    if (!readArgs(argc - 2, argv + 2, a, err)) {
        if (err == "__help__") { printHelp(); return 0; }
        std::cerr << "argument error: " << err << "\n";
        return 2;
    }
    if (a.run_id.empty()) a.run_id = makeRunId();

    auto solvePair = [&](const std::string& path, int m, int n) {
        pade::io::SeriesFile sf;
        pade::StatusCode ls;
        pade::PadeResult r = loadAndSolve(path, m, n, a.tol, sf, ls);
        return std::pair{std::move(r), std::move(sf)};
    };

    if (cmd == "solve") {
        if (a.file.empty() || a.m < 0 || a.n < 0) {
            std::cerr << "solve requires --file, --m, --n\n";
            return 2;
        }
        auto [r, sf] = solvePair(a.file, a.m, a.n);
        std::cout << pade::io::renderReport(a.run_id, sf.name.empty() ? a.file : sf.name,
                                            PADE_VERSION, sf.coeffs, r, a.verbose);
        return exitFor(r.status);
    }

    if (cmd == "eval") {
        if (a.file.empty() || a.m < 0 || a.n < 0) {
            std::cerr << "eval requires --file, --m, --n, --x\n";
            return 2;
        }
        auto [r, sf] = solvePair(a.file, a.m, a.n);
        if (r.status != pade::StatusCode::kOk &&
            r.status != pade::StatusCode::kRankDeficient) {
            std::cout << pade::io::renderReport(a.run_id, sf.name, PADE_VERSION,
                                                sf.coeffs, r, a.verbose);
            return exitFor(r.status);
        }
        pade::EvalResult e = pade::evaluatePade(r, a.x);
        std::cout << "run_id  : " << a.run_id << "\n"
                  << "version : " << PADE_VERSION << "\n"
                  << "input   : " << sf.name << "\n"
                  << "x       : " << a.x << "\n"
                  << "status  : " << pade::statusName(e.status) << "\n"
                  << "Q(x)    : " << e.denominator << "\n"
                  << "P/Q     : " << e.value << "\n"
                  << "message : " << e.message << "\n";
        return exitFor(e.status);
    }

    if (cmd == "serve") {
        std::cout << "pade-serve " << PADE_VERSION
                  << " run_id=" << a.run_id << " READY\n";
        std::string line;
        int lineNo = 0;
        while (std::getline(std::cin, line)) {
            ++lineNo;
            std::istringstream is(line);
            std::string op, path;
            int m, n;
            is >> op;
            if (op.empty() || op[0] == '#') continue;
            if (op == "QUIT") { std::cout << "BYE\n"; return 0; }
            if (op == "SOLVE") {
                is >> path >> m >> n;
                auto [r, sf] = solvePair(path, m, n);
                std::cout << "#" << lineNo << " run_id=" << a.run_id
                          << " input=" << (sf.name.empty() ? path : sf.name)
                          << " status=" << pade::statusName(r.status)
                          << " verdict="
                          << (r.status == pade::StatusCode::kOk
                                  ? (r.residual.matches_to_order
                                         ? "MATCH_TO_ORDER"
                                         : "RESIDUAL_MISMATCH")
                                  : pade::statusName(r.status))
                          << " max|r|=" << r.residual.max_abs_residual << "\n";
                std::cout << pade::io::renderReport(
                    a.run_id + ":" + std::to_string(lineNo),
                    sf.name.empty() ? path : sf.name, PADE_VERSION, sf.coeffs,
                    r, false);
            } else if (op == "EVAL") {
                double x;
                is >> path >> m >> n >> x;
                auto [r, sf] = solvePair(path, m, n);
                if (r.status != pade::StatusCode::kOk &&
                    r.status != pade::StatusCode::kRankDeficient) {
                    std::cout << "#" << lineNo << " status="
                              << pade::statusName(r.status) << " " << r.message
                              << "\n";
                    continue;
                }
                pade::EvalResult e = pade::evaluatePade(r, x);
                std::cout << "#" << lineNo << " run_id=" << a.run_id
                          << " input=" << sf.name << " x=" << x
                          << " status=" << pade::statusName(e.status)
                          << " Q=" << e.denominator << " P/Q=" << e.value
                          << "\n";
            } else {
                std::cout << "#" << lineNo
                          << " status=INVALID_ARGUMENT unknown op: " << op
                          << "\n";
            }
        }
        std::cout << "pade-serve EOF\n";
        return 0;
    }

    std::cerr << "unknown command: " << cmd << "\n";
    printHelp();
    return 2;
}
