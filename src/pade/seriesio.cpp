#include "pade/seriesio.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace pade::series {

Kind parseKind(const std::string& name) {
    std::string n;
    for (char ch : name) n.push_back(static_cast<char>(std::tolower(ch)));
    if (n == "exp") return Kind::Exp;
    if (n == "sin") return Kind::Sin;
    if (n == "cos") return Kind::Cos;
    if (n == "geometric") return Kind::Geometric;
    if (n == "polynomial") return Kind::Polynomial;
    throw std::invalid_argument("unknown series kind: " + name);
}

const char* kindName(Kind k) {
    switch (k) {
        case Kind::Exp: return "exp";
        case Kind::Sin: return "sin";
        case Kind::Cos: return "cos";
        case Kind::Geometric: return "geometric";
        case Kind::Polynomial: return "polynomial";
    }
    return "unknown";
}

std::vector<Real> generate(Kind kind, int terms, const std::vector<Real>& params) {
    if (terms <= 0) throw std::invalid_argument("terms must be positive");
    std::vector<Real> c(terms, Real(0));
    switch (kind) {
        case Kind::Exp: {
            Real fact = 1;
            for (int k = 0; k < terms; ++k) { c[k] = 1 / fact; fact *= k + 1; }
            break;
        }
        case Kind::Sin:
            // sin x = x - x^3/3! + x^5/5! - ...
            for (int j = 0;; ++j) {
                const int k = 2 * j + 1;
                if (k >= terms) break;
                Real fact = 1;
                for (int t = 2; t <= k; ++t) fact *= t;
                c[k] = (j % 2 ? -1 : 1) / fact;
            }
            break;
        case Kind::Cos:
            for (int j = 0;; ++j) {
                const int k = 2 * j;
                if (k >= terms) break;
                Real fact = 1;
                for (int t = 2; t <= k; ++t) fact *= t;
                c[k] = (j % 2 ? -1 : 1) / fact;
            }
            break;
        case Kind::Geometric: {
            // 1/(1-r x): c_k = r^k; r defaults to 1.
            const Real r = params.empty() ? Real(1) : params[0];
            Real rk = 1;
            for (int k = 0; k < terms; ++k) { c[k] = rk; rk *= r; }
            break;
        }
        case Kind::Polynomial:
            // params are the polynomial coefficients c_0, c_1, ...
            for (int k = 0; k < terms && k < static_cast<int>(params.size()); ++k)
                c[k] = params[k];
            break;
    }
    return c;
}

std::vector<Real> loadCoefficients(const std::string& path, std::string& error) {
    std::ifstream f(path);
    if (!f) { error = "cannot open coefficients file: " + path; return {}; }
    std::vector<Real> c;
    std::string line;
    int lineno = 0;
    while (std::getline(f, line)) {
        ++lineno;
        const auto hash = line.find('#');
        if (hash != std::string::npos) line.erase(hash);
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
        try {
            size_t pos = 0;
            const Real v = std::stold(line, &pos);
            if (line.find_first_not_of(" \t\r\n", pos) != std::string::npos)
                throw std::invalid_argument("trailing characters");
            c.push_back(v);
        } catch (const std::exception& e) {
            error = "line " + std::to_string(lineno) + ": bad coefficient '" +
                    line + "' (" + e.what() + ")";
            return {};
        }
    }
    if (c.empty()) error = "coefficients file contained no data: " + path;
    return c;
}

bool saveCoefficients(const std::string& path, const std::vector<Real>& c,
                      std::string& error) {
    std::ofstream f(path);
    if (!f) { error = "cannot write coefficients file: " + path; return false; }
    f << "# synthetic Pade series coefficients (long double, one per line)\n";
    f << "# count=" << c.size() << "\n";
    f << std::setprecision(21);
    for (Real v : c) f << std::hexfloat << v << "\n";
    return static_cast<bool>(f);
}

} // namespace pade::series
