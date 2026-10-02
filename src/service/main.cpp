// pade_cli: runnable service entry point for the Pade engine.
//
// Subcommands:
//   version
//   gen-series --kind exp --terms 12 [--param r] [--out file]
//   approx --m 2 --n 2 --series file | --kind exp [--terms N] [--config file]
//   eval   --run-id ID --x 0.1            (reads a JSON report on stdin is not
//                                          supported; instead use approx+eval in
//                                          one invocation: --eval x)
//   serve  --port 8080                     (minimal local HTTP JSON service)
//
// Exit codes: 0 ok or rank-deficient-but-defined; 2 invalid arguments;
//             3 degenerate / numerical failure; 4 denominator near zero.
#include "pade/config.hpp"
#include "pade/logging.hpp"
#include "pade/seriesio.hpp"
#include "pade/service.hpp"
#include "pade/solver.hpp"
#include "pade/version.hpp"

#include <atomic>
#include <cstring>
#include <iostream>
#include <map>
#include <signal.h>
#include <string>
#include <thread>
#include <sstream>
#include <vector>

#ifdef __linux__
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

using namespace pade;

namespace {

std::map<std::string, std::string> parseArgs(int argc, char** argv) {
    std::map<std::string, std::string> a;
    for (int i = 2; i < argc; ++i) {
        std::string tok = argv[i];
        if (tok.rfind("--", 0) == 0) {
            const std::string key = tok.substr(2);
            if (i + 1 < argc && std::strncmp(argv[i + 1], "--", 2) != 0)
                a[key] = argv[++i];
            else
                a[key] = "true";
        } else {
            a["_positional"] = tok;
        }
    }
    return a;
}

int exitFor(StatusCode s) {
    switch (s) {
        case StatusCode::Ok: return 0;
        case StatusCode::RankDeficient: return 0;
        case StatusCode::InvalidArgument: return 2;
        case StatusCode::DegenerateDenominatorConstant: return 3;
        case StatusCode::NumericalFailure: return 3;
        case StatusCode::DenominatorNearZero: return 4;
        default: return 5;
    }
}

int cmdVersion() {
    std::cout << "{\"name\":\"pade_rational\",\"version\":\"" PADE_VERSION
              << "\",\"engine\":\"" PADE_ENGINE "\",\"standard\":\"C++20\"}\n";
    return 0;
}

int cmdGenSeries(const std::map<std::string, std::string>& a) {
    if (!a.count("kind")) {
        std::cerr << "gen-series requires --kind (exp|sin|cos|geometric|polynomial)\n";
        return 2;
    }
    const int terms = a.count("terms") ? std::stoi(a.at("terms")) : 10;
    series::Kind kind;
    try { kind = series::parseKind(a.at("kind")); }
    catch (const std::exception& e) { std::cerr << e.what() << "\n"; return 2; }
    std::vector<Real> params;
    if (a.count("param")) params.push_back(std::stold(a.at("param")));
    std::vector<Real> c;
    try { c = series::generate(kind, terms, params); }
    catch (const std::exception& e) { std::cerr << e.what() << "\n"; return 2; }
    if (a.count("out")) {
        std::string err;
        if (!series::saveCoefficients(a.at("out"), c, err)) {
            std::cerr << err << "\n"; return 2;
        }
        std::cerr << "wrote " << c.size() << " coefficients to " << a.at("out") << "\n";
    } else {
        for (Real v : c) std::cout << std::hexfloat << v << "\n";
    }
    return 0;
}

int cmdApprox(const std::map<std::string, std::string>& a) {
    if (!a.count("m") || !a.count("n")) {
        std::cerr << "approx requires --m and --n\n"; return 2;
    }
    int m, n;
    try { m = std::stoi(a.at("m")); n = std::stoi(a.at("n")); }
    catch (const std::exception&) { std::cerr << "bad --m/--n\n"; return 2; }

    cfg::Config config;
    if (a.count("config")) {
        config = cfg::loadFile(a.at("config"));
        if (!config.parse_errors.empty()) {
            for (const auto& e : config.parse_errors) std::cerr << "config: " << e << "\n";
            return 2;
        }
        if (!config.unknown_keys.empty()) {
            for (const auto& k : config.unknown_keys)
                std::cerr << "config: ignoring unknown key " << k << "\n";
        }
    }

    std::vector<Real> c;
    std::string source;
    if (a.count("series")) {
        std::string err;
        c = series::loadCoefficients(a.at("series"), err);
        if (!err.empty()) { std::cerr << err << "\n"; return 2; }
        source = "file:" + a.at("series");
    } else if (a.count("kind")) {
        const int need = m + n + 1 + config.series_extra_terms;
        const int terms = a.count("terms") ? std::stoi(a.at("terms")) : need;
        try {
            std::vector<Real> params;
            if (a.count("param")) params.push_back(std::stold(a.at("param")));
            c = series::generate(series::parseKind(a.at("kind")), terms, params);
        } catch (const std::exception& e) { std::cerr << e.what() << "\n"; return 2; }
        source = std::string("synthetic:") + a.at("kind");
    } else {
        std::cerr << "approx requires --series <file> or --kind <name>\n";
        return 2;
    }

    SeriesRequest req;
    req.m = m; req.n = n; req.coefficients = c; req.options = config.solve;
    std::cerr << "pade approx source=" << source << " terms=" << c.size()
              << " run=" ;
    SolveReport r = solvePade(req);
    std::cerr << r.run_id << " status=" << toString(r.status) << "\n";
    std::cout << svc::reportToJson(r) << "\n";

    int rc = exitFor(r.status);
    if (a.count("eval")) {
        const Real x = std::stold(a.at("eval"));
        EvalReport e = evaluate(r, x, config.pole_relative_tol);
        std::cerr << "eval run=" << r.run_id << " x=" << static_cast<double>(x)
                  << " status=" << toString(e.status) << "\n";
        std::cout << svc::evalToJson(e, r.run_id) << "\n";
        if (isFailure(e.status)) rc = exitFor(e.status);
    }
    return rc;
}

#ifdef __linux__
volatile sig_atomic_t g_stop = 0;
void onSig(int) { g_stop = 1; }

// Minimal local HTTP service. One request at a time, JSON in / JSON out,
// bound to loopback only. Request body fields: m,n,coefficients[],optional opts.
int cmdServe(const std::map<std::string, std::string>& a) {
    const int port = a.count("port") ? std::stoi(a.at("port")) : 8080;
    signal(SIGINT, onSig);
    signal(SIGTERM, onSig);

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { std::cerr << "socket() failed\n"; return 3; }
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (bind(srv, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::cerr << "bind() failed on 127.0.0.1:" << port << "\n";
        close(srv);
        return 3;
    }
    listen(srv, 4);
    std::cerr << "pade serve listening on http://127.0.0.1:" << port
              << " (POST /approx JSON; GET /health; Ctrl-C stops)\n";

    while (!g_stop) {
        int cli = accept(srv, nullptr, nullptr);
        if (cli < 0) continue;
        // Read until headers are complete, honor Content-Length for the body.
        std::string req;
        size_t header_end = std::string::npos;
        size_t content_length = 0;
        for (;;) {
            char buf[4096];
            ssize_t k = recv(cli, buf, sizeof(buf), 0);
            if (k <= 0) break;
            req.append(buf, static_cast<size_t>(k));
            const auto a = req.find("\r\n\r\n");
            const auto b = req.find("\n\n");
            header_end = (a != std::string::npos)
                             ? a + 4
                             : (b != std::string::npos ? b + 2 : std::string::npos);
            if (header_end != std::string::npos) {
                const std::string head = req.substr(0, header_end);
                const auto cl = head.find("Content-Length:");
                if (cl == std::string::npos) {
                    const auto cl2 = head.find("content-length:");
                    if (cl2 != std::string::npos)
                        content_length = static_cast<size_t>(
                            std::stoul(head.c_str() + cl2 + 15));
                } else {
                    content_length = static_cast<size_t>(
                        std::stoul(head.c_str() + cl + 15));
                }
                if (req.size() - header_end >= content_length) break;
            }
        }
        const bool get = req.rfind("GET ", 0) == 0;
        std::string body;
        if (header_end != std::string::npos)
            body = req.substr(header_end, content_length);

        std::string status_line, payload, ctype = "application/json";
        if (get && req.find(" /health") != std::string::npos) {
            status_line = "200 OK";
            payload = std::string("{\"status\":\"up\",\"version\":\"") + PADE_VERSION + "\"}";
        } else if (req.rfind("POST /approx", 0) == 0) {
            payload = svc::handleHttpApprox(body, status_line);
        } else {
            status_line = "404 Not Found";
            payload = "{\"error\":\"unknown route; POST /approx or GET /health\"}";
        }
        std::ostringstream os;
        os << "HTTP/1.1 " << status_line << "\r\n"
           << "Content-Type: " << ctype << "\r\n"
           << "Content-Length: " << payload.size() << "\r\n"
           << "Connection: close\r\n\r\n" << payload;
        const std::string out = os.str();
        send(cli, out.data(), out.size(), 0);
        close(cli);
    }
    close(srv);
    std::cerr << "pade serve stopped\n";
    return 0;
}
#else
int cmdServe(const std::map<std::string,std::string>&) {
    std::cerr << "serve is only supported on Linux in this build\n"; return 3;
}
#endif

} // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: pade_cli {version|gen-series|approx|serve} ...\n";
        return 2;
    }
    const std::string cmd = argv[1];
    const auto a = parseArgs(argc, argv);
    if (cmd == "version") return cmdVersion();
    if (cmd == "gen-series") return cmdGenSeries(a);
    if (cmd == "approx") return cmdApprox(a);
    if (cmd == "serve") return cmdServe(a);
    std::cerr << "unknown subcommand: " << cmd << "\n";
    return 2;
}
