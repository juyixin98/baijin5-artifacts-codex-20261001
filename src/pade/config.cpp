#include "pade/config.hpp"
#include <cctype>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace pade::cfg {
namespace {

bool setReal(const std::string& key, const std::string& val, Real& out,
             std::vector<std::string>& errs) {
    try {
        size_t pos = 0;
        const std::string v = val.substr(val.find_first_not_of(" \t"));
        long double x = std::stold(v, &pos);
        if (pos != v.size()) throw std::invalid_argument("trailing characters");
        out = static_cast<Real>(x);
        return true;
    } catch (const std::exception& e) {
        errs.push_back(key + ": invalid real '" + val + "' (" + e.what() + ")");
        return false;
    }
}

bool setInt(const std::string& key, const std::string& val, int& out,
            std::vector<std::string>& errs) {
    try {
        size_t pos = 0;
        const std::string v = val.substr(val.find_first_not_of(" \t"));
        int x = std::stoi(v, &pos);
        if (pos != v.size()) throw std::invalid_argument("trailing characters");
        out = x;
        return true;
    } catch (const std::exception& e) {
        errs.push_back(key + ": invalid integer '" + val + "' (" + e.what() + ")");
        return false;
    }
}

bool setBool(const std::string& key, const std::string& val, bool& out,
             std::vector<std::string>& errs) {
    std::string v;
    for (char ch : val) v.push_back(static_cast<char>(std::tolower(ch)));
    v.erase(0, v.find_first_not_of(" \t"));
    while (!v.empty() && (v.back() == ' ' || v.back() == '\t' || v.back() == '\r'))
        v.pop_back();
    if (v == "true" || v == "1" || v == "on") { out = true; return true; }
    if (v == "false" || v == "0" || v == "off") { out = false; return true; }
    errs.push_back(key + ": invalid boolean '" + val + "'");
    return false;
}

} // namespace

Config parse(const std::string& text, const Config& base) {
    Config c = base;
    std::istringstream is(text);
    std::string line;
    int lineno = 0;
    while (std::getline(is, line)) {
        ++lineno;
        const auto hash = line.find('#');
        if (hash != std::string::npos) line.erase(hash);
        const auto eq = line.find('=');
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) continue;
        if (eq == std::string::npos) {
            c.parse_errors.push_back("line " + std::to_string(lineno) +
                                     ": expected key=value, got '" + line + "'");
            continue;
        }
        std::string key = line.substr(0, eq);
        std::string val = line.substr(eq + 1);
        key.erase(key.find_last_not_of(" \t") + 1);
        const auto ks = key.find_first_not_of(" \t");
        key = key.substr(ks);

        bool known = true;
        if (key == "rank_tol_factor")      setReal(key, val, c.solve.rank_tol_factor, c.parse_errors);
        else if (key == "residual_tol")    setReal(key, val, c.solve.residual_tol, c.parse_errors);
        else if (key == "gcd_tol")         setReal(key, val, c.solve.gcd_tol, c.parse_errors);
        else if (key == "residual_extra_terms")
            setInt(key, val, c.solve.residual_extra_terms, c.parse_errors);
        else if (key == "remove_common_factor")
            setBool(key, val, c.solve.remove_common_factor, c.parse_errors);
        else if (key == "pole_relative_tol")
            setReal(key, val, c.pole_relative_tol, c.parse_errors);
        else if (key == "series_extra_terms")
            setInt(key, val, c.series_extra_terms, c.parse_errors);
        else known = false;

        if (!known) c.unknown_keys.push_back(key + " (line " + std::to_string(lineno) + ")");
    }
    return c;
}

Config loadFile(const std::string& path, const Config& base) {
    std::ifstream f(path);
    if (!f) {
        Config c = base;
        c.parse_errors.push_back("cannot open config file: " + path);
        return c;
    }
    std::ostringstream ss;
    ss << f.rdbuf();
    return parse(ss.str(), base);
}

std::string dump(const Config& c) {
    std::ostringstream os;
    os << std::setprecision(6);
    os << "rank_tol_factor=" << static_cast<double>(c.solve.rank_tol_factor) << "\n"
       << "residual_tol=" << static_cast<double>(c.solve.residual_tol) << "\n"
       << "gcd_tol=" << static_cast<double>(c.solve.gcd_tol) << "\n"
       << "residual_extra_terms=" << c.solve.residual_extra_terms << "\n"
       << "remove_common_factor=" << (c.solve.remove_common_factor ? "true" : "false") << "\n"
       << "pole_relative_tol=" << static_cast<double>(c.pole_relative_tol) << "\n"
       << "series_extra_terms=" << c.series_extra_terms << "\n";
    return os.str();
}

} // namespace pade::cfg
