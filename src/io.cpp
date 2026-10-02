#include "pade/io.hpp"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>

#include "pade/polynomial.hpp"

namespace pade::io {

namespace {
std::string trimWs(std::string s) {
    auto a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    auto b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}
} // namespace

StatusCode loadSeriesFile(const std::string& path, SeriesFile& out) {
    std::ifstream in(path);
    if (!in) return StatusCode::kIoError;
    out = {};
    std::string line;
    while (std::getline(in, line)) {
        auto hash = line.find('#');
        if (hash != std::string::npos) line.erase(hash);
        line = trimWs(line);
        if (line.empty()) continue;
        auto eq = line.find('=');
        if (eq == std::string::npos) return StatusCode::kInvalidArgument;
        std::string key = trimWs(line.substr(0, eq));
        std::string val = trimWs(line.substr(eq + 1));
        if (key == "name") {
            out.name = val;
        } else if (key == "coefficients") {
            std::stringstream ss(val);
            std::string tok;
            while (std::getline(ss, tok, ',')) {
                tok = trimWs(tok);
                if (tok.empty()) continue;
                try {
                    std::size_t used = 0;
                    double v = std::stod(tok, &used);
                    if (used == 0 || !std::isfinite(v))
                        return StatusCode::kInvalidArgument;
                    out.coeffs.push_back(v);
                } catch (...) {
                    return StatusCode::kInvalidArgument;
                }
            }
        } else {
            return StatusCode::kInvalidArgument;
        }
    }
    if (out.coeffs.empty()) return StatusCode::kInvalidArgument;
    if (out.name.empty()) out.name = path;
    return StatusCode::kOk;
}

std::string formatCoeffs(const std::vector<Real>& c, int precision) {
    std::ostringstream os;
    os << '[';
    for (std::size_t i = 0; i < c.size(); ++i) {
        if (i) os << ", ";
        os << std::setprecision(precision) << c[i];
    }
    os << ']';
    return os.str();
}

std::string renderReport(const std::string& run_id,
                         const std::string& input_name,
                         const std::string& tool_version,
                         const std::vector<Real>& coeffs,
                         const PadeResult& r,
                         bool verbose) {
    std::ostringstream os;
    os << std::setprecision(15);
    os << "=== pade-cli report ===\n";
    os << "run_id   : " << run_id << "\n";
    os << "input    : " << input_name << "\n";
    os << "version  : " << tool_version << "\n";
    os << "request  : [" << r.requested_m << "/" << r.requested_n << "]\n";
    os << "series_c : " << formatCoeffs(coeffs) << "\n";
    os << "status   : " << statusName(r.status) << "\n";
    os << "message  : " << r.message << "\n";

    os << "[step] rank diagnostics\n";
    os << "  Toeplitz block : n=" << r.rank.requested_n
       << " numerical_rank=" << r.rank.numerical_rank
       << " nullity=" << r.rank.nullity
       << " (classic C rank=" << r.rank.truncated_rank << ")\n";
    os << "  singular values: sigma_max=" << r.rank.sigma_max
       << " sigma_min=" << r.rank.sigma_min
       << " tol=" << r.rank.effective_tol << "\n";

    os << "[step] normalization (b0=1)\n";
    os << "  normalized=" << (r.normalized ? "true" : "false") << "\n";
    if (!r.denominator_full.empty())
        os << "  denominator_full[0]=" << r.denominator_full[0] << "\n";

    os << "[step] local definition at requested order (never erased)\n";
    os << "  numerator_full   = " << formatCoeffs(r.numerator_full) << "\n";
    os << "  denominator_full = " << formatCoeffs(r.denominator_full) << "\n";

    os << "[step] common-factor elimination\n";
    os << "  monomial_shift=" << r.reduction.monomial_shift
       << " polynomial_gcd_degree=" << r.reduction.polynomial_gcd_degree
       << " reduced=[" << r.reduction.reduced_m << "/"
       << r.reduction.reduced_n << "]\n";
    if (r.reduction.polynomial_gcd_degree > 0)
        os << "  gcd(monic) = " << formatCoeffs(r.reduction.gcd_coeffs) << "\n";
    os << "  numerator_reduced   = " << formatCoeffs(r.numerator_reduced)
       << "\n";
    os << "  denominator_reduced = " << formatCoeffs(r.denominator_reduced)
       << "\n";

    os << "[step] residual verification term by term (r_k = (C*B)_k - a_k)\n";
    for (std::size_t k = 0; k < r.residual.residual.size(); ++k) {
        os << "  r_" << k << " = " << r.residual.residual[k] << "\n";
    }
    os << "  max|r_k| (k=0.." << r.residual.checked_through
       << ") = " << r.residual.max_abs_residual << "\n";
    if (r.residual.next_term_available)
        os << "  r_{" << (r.residual.checked_through + 1)
           << "} (should be nonzero generically) = "
           << r.residual.next_term_residual << "\n";
    os << "verdict  : "
       << (r.status == StatusCode::kOk
               ? (r.residual.matches_to_order ? "MATCH_TO_ORDER"
                                              : "RESIDUAL_MISMATCH")
               : statusName(r.status))
       << "\n";
    (void)verbose;
    return os.str();
}

} // namespace pade::io
