#include "gauss/error.hpp"

#include <sstream>

namespace gauss {

std::string_view to_string(ErrorCategory category) noexcept {
  switch (category) {
    case ErrorCategory::kInvalidInput:
      return "invalid_input";
    case ErrorCategory::kStateConflict:
      return "state_conflict";
    case ErrorCategory::kResourceExhaustion:
      return "resource_exhaustion";
    case ErrorCategory::kComputationFailure:
      return "computation_failure";
  }
  return "unknown";
}

std::string Error::to_string() const {
  std::ostringstream os;
  os << gauss::to_string(category) << ": " << message;
  for (const auto& [key, value] : diagnostics) {
    os << "\n  " << key << " = " << value;
  }
  return os.str();
}

}  // namespace gauss
