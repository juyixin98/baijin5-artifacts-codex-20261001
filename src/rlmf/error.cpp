#include "rlmf/error.hpp"

namespace rlmf {

const char* error_kind_name(ErrorKind kind) noexcept {
    switch (kind) {
    case ErrorKind::None: return "None";
    case ErrorKind::InvalidArgument: return "InvalidArgument";
    case ErrorKind::StateConflict: return "StateConflict";
    case ErrorKind::ResourceExhausted: return "ResourceExhausted";
    case ErrorKind::ComputationFailed: return "ComputationFailed";
    }
    return "Unknown";
}

std::ostream& operator<<(std::ostream& os, const Error& err) {
    os << '[' << error_kind_name(err.kind) << ' ' << err.code << "] "
       << err.message;
    return os;
}

} // namespace rlmf
