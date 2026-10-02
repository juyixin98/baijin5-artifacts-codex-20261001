#pragma once
// Service layer: JSON rendering and command dispatch shared by the CLI and the
// minimal HTTP entry point. No third-party JSON library is used; output is a
// strict, machine-readable subset with explicit status strings (never a blanket
// "success" when a failure/warning category applies).
#include "pade/types.hpp"
#include <string>

namespace pade::svc {

std::string escapeJson(const std::string& s);
std::string vecToJson(const Vector& v);
std::string reportToJson(const SolveReport& r);
std::string evalToJson(const EvalReport& e, const std::string& run_id);

// Parse "m/n" into orders; returns "" on success or an error message.
std::string parseOrders(const std::string& s, int& m, int& n);

// Minimal request parser used by the HTTP entry point. Accepts a JSON object
// with integer "m","n" and a number array "coefficients". Returns an HTTP
// status line ("200 OK" / "400 Bad Request") and the JSON response body.
std::string handleHttpApprox(const std::string& body, std::string& http_status);

} // namespace pade::svc
