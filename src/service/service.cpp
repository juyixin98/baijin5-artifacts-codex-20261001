#include "pade/service.hpp"
#include "pade/solver.hpp"
#include <cctype>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <sstream>

namespace pade::svc {

std::string escapeJson(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 4);
    for (char ch : s) {
        switch (ch) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                    o += buf;
                } else o += ch;
        }
    }
    return o;
}

std::string vecToJson(const Vector& v) {
    std::ostringstream os;
    os << std::setprecision(18) << "[";
    for (int i = 0; i < v.size(); ++i) {
        if (i) os << ",";
        os << static_cast<double>(v[i]);
    }
    os << "]";
    return os.str();
}

std::string reportToJson(const SolveReport& r) {
    std::ostringstream os;
    os << "{"
       << "\"run_id\":\"" << escapeJson(r.run_id) << "\","
       << "\"status\":\"" << toString(r.status) << "\","
       << "\"failure\":" << (isFailure(r.status) ? "true" : "false") << ","
       << "\"reason\":\"" << escapeJson(r.reason) << "\","
       << "\"m\":" << r.m << ",\"n\":" << r.n
       << ",\"required_series_len\":" << r.required_series_len
       << ",\"matrix_rank\":" << r.matrix_rank
       << ",\"condition_number\":" << static_cast<double>(r.condition_number)
       << ",\"singular_values\":" << vecToJson(r.singular_values)
       << ",\"q0_normalized\":" << (r.q0_normalized ? "true" : "false")
       << ",\"p\":" << vecToJson(r.p)
       << ",\"q\":" << vecToJson(r.q)
       << ",\"residual\":" << vecToJson(r.residual)
       << ",\"matched_terms\":" << r.matched_terms
       << ",\"residual_verified\":" << (r.residual_verified ? "true" : "false")
       << ",\"gcd_degree\":" << r.gcd_degree
       << ",\"gcd_coeffs\":" << vecToJson(r.gcd_coeffs)
       << ",\"p_reduced\":" << vecToJson(r.p_reduced)
       << ",\"q_reduced\":" << vecToJson(r.q_reduced)
       << ",\"common_factor_removed\":" << (r.common_factor_removed ? "true" : "false")
       << "}";
    return os.str();
}

std::string evalToJson(const EvalReport& e, const std::string& run_id) {
    std::ostringstream os;
    os << "{"
       << "\"run_id\":\"" << escapeJson(run_id) << "\","
       << "\"status\":\"" << toString(e.status) << "\","
       << "\"failure\":" << (isFailure(e.status) ? "true" : "false") << ","
       << "\"reason\":\"" << escapeJson(e.reason) << "\","
       << "\"x\":" << std::setprecision(18) << static_cast<double>(e.x)
       << ",\"value\":" << static_cast<double>(e.value)
       << ",\"numerator\":" << static_cast<double>(e.numerator)
       << ",\"denominator\":" << static_cast<double>(e.denominator)
       << ",\"used_reduced\":" << (e.used_reduced ? "true" : "false")
       << "}";
    return os.str();
}

std::string parseOrders(const std::string& s, int& m, int& n) {
    const auto slash = s.find('/');
    if (slash == std::string::npos)
        return "orders must look like m/n, got '" + s + "'";
    try {
        m = std::stoi(s.substr(0, slash));
        n = std::stoi(s.substr(slash + 1));
    } catch (const std::exception&) {
        return "non-integer orders in '" + s + "'";
    }
    if (m < 0 || n < 0) return "orders must be non-negative in '" + s + "'";
    return "";
}

namespace {

struct MiniJson {
    const std::string& s;
    size_t i = 0;
    explicit MiniJson(const std::string& str) : s(str) {}
    void skipWs() {
        while (i < s.size() &&
               (s[i]==' '||s[i]=='\t'||s[i]=='\n'||s[i]=='\r')) ++i;
    }
    bool consume(char ch) {
        skipWs();
        if (i < s.size() && s[i] == ch) { ++i; return true; }
        return false;
    }
    bool readNumber(long double& out) {
        skipWs();
        const size_t start = i;
        if (i < s.size() && (s[i] == '-' || s[i] == '+')) ++i;
        bool any = false;
        while (i < s.size()) {
            const char ch = s[i];
            if (std::isdigit(static_cast<unsigned char>(ch)) || ch == '.' ||
                ch == 'e' || ch == 'E' || ch == '+' || ch == '-') { ++i; any = true; }
            else break;
        }
        if (!any) return false;
        try { out = std::stold(s.substr(start, i - start)); }
        catch (...) { return false; }
        return true;
    }
    bool readInt(int& out) {
        long double x = 0;
        if (!readNumber(x)) return false;
        out = static_cast<int>(x);
        return true;
    }
    bool readString(std::string& out) {
        skipWs();
        if (i >= s.size() || s[i] != '"') return false;
        ++i;
        out.clear();
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\' && i + 1 < s.size()) { out += s[i + 1]; i += 2; }
            else out += s[i++];
        }
        return i < s.size() && s[i++] == '"';
    }
    void skipValue() {
        skipWs();
        if (i >= s.size()) return;
        const char ch = s[i];
        if (ch == '"') { std::string tmp; readString(tmp); return; }
        if (ch == '[' || ch == '{') {
            const char close = ch == '[' ? ']' : '}';
            int depth = 0;
            do {
                const char c = s[i++];
                if (c == ch) ++depth;
                else if (c == close) --depth;
            } while (i < s.size() && depth > 0);
            return;
        }
        while (i < s.size() && s[i] != ',' && s[i] != '}') ++i;
    }
};

} // namespace

std::string handleHttpApprox(const std::string& body, std::string& http_status) {
    http_status = "400 Bad Request";
    MiniJson j(body);
    if (!j.consume('{')) return R"({"error":"expected JSON object"})";

    int m = -1, n = -1;
    std::vector<Real> coeffs;
    bool haveC = false;

    j.skipWs();
    if (j.consume('}'))
        return R"({"error":"empty request; need m,n,coefficients"})";

    for (;;) {
        std::string key;
        if (!j.readString(key)) return R"({"error":"expected key string"})";
        if (!j.consume(':')) return R"({"error":"expected ':'"})";
        if (key == "m") {
            if (!j.readInt(m)) return R"({"error":"bad integer m"})";
        } else if (key == "n") {
            if (!j.readInt(n)) return R"({"error":"bad integer n"})";
        } else if (key == "coefficients") {
            if (!j.consume('[')) return R"({"error":"coefficients must be an array"})";
            haveC = true;
            j.skipWs();
            if (!j.consume(']')) {
                for (;;) {
                    long double v = 0;
                    if (!j.readNumber(v)) return R"({"error":"bad coefficient value"})";
                    coeffs.push_back(static_cast<Real>(v));
                    if (j.consume(']')) break;
                    if (!j.consume(',')) return R"({"error":"expected ',' or ']'"})";
                }
            }
        } else {
            j.skipValue();
        }
        if (j.consume(',')) continue;
        if (j.consume('}')) break;
        return R"({"error":"expected ',' or '}'"})";
    }

    if (m < 0 || n < 0)
        return R"({"error":"m and n are required and non-negative"})";
    if (!haveC) return R"({"error":"coefficients array is required"})";

    SeriesRequest req;
    req.m = m; req.n = n; req.coefficients = coeffs;
    SolveReport r = solvePade(req);
    http_status = isFailure(r.status) ? "422 Unprocessable Entity" : "200 OK";
    return reportToJson(r);
}

} // namespace pade::svc
