#include "procrustes/explain.hpp"

#include <iomanip>
#include <sstream>

namespace procrustes {
namespace {

std::string jsonEscape(const std::string& in) {
  std::string out;
  out.reserve(in.size() + 2);
  for (char c : in) {
    switch (c) {
      case '"':
        out += "\\\"";
        break;
      case '\\':
        out += "\\\\";
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          std::ostringstream hex;
          hex << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<int>(static_cast<unsigned char>(c));
          out += hex.str();
        } else {
          out += c;
        }
    }
  }
  return out;
}

std::string matrixToJson(const Eigen::MatrixXd& m) {
  std::ostringstream os;
  os << "[";
  for (int i = 0; i < m.rows(); ++i) {
    os << (i == 0 ? "" : ",") << "[";
    for (int j = 0; j < m.cols(); ++j) {
      os << (j == 0 ? "" : ",") << m(i, j);
    }
    os << "]";
  }
  os << "]";
  return os.str();
}

std::string vectorToJson(const Eigen::VectorXd& v) {
  std::ostringstream os;
  os << "[";
  for (int i = 0; i < v.size(); ++i) {
    os << (i == 0 ? "" : ",") << v(i);
  }
  os << "]";
  return os.str();
}

}  // namespace

std::string renderText(const FitResult& result,
                       const ExplainOptions& options) {
  std::ostringstream os;
  const std::string ind = options.indent;
  os << ind << "FitResult request_id="
     << (result.request_id.empty() ? "-" : result.request_id)
     << " version=" << result.version << '\n';
  os << ind << "  status: " << (result.ok ? "ok" : "FAILED")
     << " mode=" << toString(result.mode)
     << " d=" << result.dimension << " n=" << result.point_count << '\n';

  // Failures live in their own section.
  bool has_error = false;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity == Severity::Error) {
      if (!has_error) {
        os << ind << "  failures:\n";
        has_error = true;
      }
      os << ind << "    - [" << toString(dg.code) << "] " << dg.step << ": "
         << dg.message;
      if (!dg.detail.empty()) os << " (" << dg.detail << ")";
      os << '\n';
    }
  }
  if (result.ok && !has_error) {
    os << ind << "  failures: none\n";
  }

  // Uncertain / non-unique conclusions are explicitly separated.
  bool has_warning = false;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity == Severity::Warning) {
      if (!has_warning) {
        os << ind << "  uncertainties:\n";
        has_warning = true;
      }
      os << ind << "    - [" << toString(dg.code) << "] " << dg.step << ": "
         << dg.message;
      if (!dg.detail.empty()) os << " (" << dg.detail << ")";
      os << '\n';
    }
  }
  if (!has_warning) os << ind << "  uncertainties: none\n";

  if (result.ok) {
    os << ind << "  solution: q ~= s R p + t\n";
    os << ind << "    scale=" << result.scale << '\n';
    os << ind << "    det(R)=" << result.determinant << '\n';
    os << ind << "    translation=" << result.translation.transpose() << '\n';
    os << ind << "  quality:\n";
    os << ind << "    sse=" << result.sse << " rmse=" << result.rmse
       << " max_abs_residual=" << result.max_abs_residual << '\n';
    os << ind << "  identifiability: rotation_unique="
       << (result.rotation_unique ? "true" : "false")
       << " scale_identifiable="
       << (result.scale_identifiable ? "true" : "false") << '\n';
  }

  if (options.show_steps) {
    os << ind << "  steps:\n";
    for (const auto& dg : result.diagnostics) {
      if (dg.severity == Severity::Info) {
        os << ind << "    - " << dg.step << ": " << dg.message;
        if (!dg.detail.empty()) os << " (" << dg.detail << ")";
        os << '\n';
      }
    }
  }
  return os.str();
}

std::string renderJson(const FitResult& result) {
  std::ostringstream os;
  os << std::setprecision(17);
  os << "{\n";
  os << "  \"request_id\": \"" << jsonEscape(result.request_id) << "\",\n";
  os << "  \"version\": \"" << jsonEscape(result.version) << "\",\n";
  os << "  \"ok\": " << (result.ok ? "true" : "false") << ",\n";
  os << "  \"code\": \"" << toString(result.code) << "\",\n";
  os << "  \"mode\": \"" << toString(result.mode) << "\",\n";
  os << "  \"dimension\": " << result.dimension << ",\n";
  os << "  \"point_count\": " << result.point_count << ",\n";

  os << "  \"failures\": [";
  bool first = true;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity != Severity::Error) continue;
    os << (first ? "\n" : ",\n");
    first = false;
    os << "    {\"code\": \"" << toString(dg.code) << "\", \"step\": \""
       << jsonEscape(dg.step) << "\", \"message\": \""
       << jsonEscape(dg.message) << "\", \"detail\": \""
       << jsonEscape(dg.detail) << "\"}";
  }
  os << (first ? "" : "\n  ") << "],\n";

  os << "  \"uncertainties\": [";
  first = true;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity != Severity::Warning) continue;
    os << (first ? "\n" : ",\n");
    first = false;
    os << "    {\"code\": \"" << toString(dg.code) << "\", \"step\": \""
       << jsonEscape(dg.step) << "\", \"message\": \""
       << jsonEscape(dg.message) << "\", \"detail\": \""
       << jsonEscape(dg.detail) << "\"}";
  }
  os << (first ? "" : "\n  ") << "],\n";

  os << "  \"steps\": [";
  first = true;
  for (const auto& dg : result.diagnostics) {
    if (dg.severity != Severity::Info) continue;
    os << (first ? "\n" : ",\n");
    first = false;
    os << "    {\"step\": \"" << jsonEscape(dg.step)
       << "\", \"message\": \"" << jsonEscape(dg.message)
       << "\", \"detail\": \"" << jsonEscape(dg.detail) << "\"}";
  }
  os << (first ? "" : "\n  ") << "],\n";

  os << "  \"rotation\": " << matrixToJson(result.rotation) << ",\n";
  os << "  \"scale\": " << result.scale << ",\n";
  os << "  \"translation\": " << vectorToJson(result.translation) << ",\n";
  os << "  \"det_rotation\": " << result.determinant << ",\n";
  os << "  \"residual_norms\": " << vectorToJson(result.residual_norms)
     << ",\n";
  os << "  \"sse\": " << result.sse << ",\n";
  os << "  \"rmse\": " << result.rmse << ",\n";
  os << "  \"max_abs_residual\": " << result.max_abs_residual << ",\n";
  os << "  \"rotation_unique\": "
     << (result.rotation_unique ? "true" : "false") << ",\n";
  os << "  \"scale_identifiable\": "
     << (result.scale_identifiable ? "true" : "false") << "\n";
  os << "}\n";
  return os.str();
}

void writeText(const FitResult& result, std::ostream& out,
               const ExplainOptions& options) {
  out << renderText(result, options);
}

}  // namespace procrustes
