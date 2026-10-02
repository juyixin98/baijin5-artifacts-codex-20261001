#include <sstream>

#include "chebcore/diagnostics.hpp"
#include "chebcore/error_estimate.hpp"
#include "chebcore/kernel.hpp"
#include "test_framework.hpp"

using namespace chebcore;
using namespace chebcore::diag;

TEST_CASE("diag:request ids are unique and carry tag") {
  auto a = new_request_id("fit");
  auto b = new_request_id("fit");
  REQUIRE(a != b);
  REQUIRE(a.rfind("fit-", 0) == 0);
  REQUIRE(b.rfind("fit-", 0) == 0);
}

TEST_CASE("diag:redact never echoes secret, shows only length/prefix") {
  std::string secret = "sk-live-1234567890ABCDEFGH";
  std::string masked = redact(secret);
  REQUIRE(masked.find("1234567890ABCDEFGH") == std::string::npos);
  REQUIRE(masked.find("***") != std::string::npos);
  REQUIRE(masked.find(std::to_string(secret.size())) != std::string::npos);
  REQUIRE(masked.rfind("sk", 0) == 0);  // only the 2-char prefix survives
  REQUIRE(redact("").find("<empty>") != std::string::npos);
}

TEST_CASE("diag:decision log states accept/reject/inconclusive with key state") {
  DecisionLog log(new_request_id("eval"));
  auto accept = evaluate_fit(
      fit_sample([](double x) { return std::exp(x); }, Interval{-1, 1}, 24),
      [](double x) { return std::exp(x); }, 20, ErrorTolerances{1e-10, 1e-10});
  log.record(Severity::Info, "VERDICT-" + std::string(to_string(accept.verdict)),
             accept.reason,
             "node=" + std::to_string(accept.node_max_abs) +
                 " holdout=" + std::to_string(accept.holdout_max_abs) +
                 " tail_l1=" + std::to_string(accept.truncation_tail_l1));
  REQUIRE_EQ(static_cast<int>(accept.verdict),
             static_cast<int>(Verdict::Accept));
  REQUIRE(!log.has_error());

  auto incon = evaluate_fit(
      fit_sample([](double x) { return std::fabs(x); }, Interval{-1, 1}, 1),
      [](double x) { return std::fabs(x); }, 2, ErrorTolerances{1e-10, 1e-8});
  REQUIRE_EQ(static_cast<int>(incon.verdict),
             static_cast<int>(Verdict::Inconclusive));
  log.record(Severity::Warn, "VERDICT-INCONCLUSIVE", incon.reason,
             "node=" + std::to_string(incon.node_max_abs) +
                 " holdout=" + std::to_string(incon.holdout_max_abs) +
                 " tail_l1=" + std::to_string(incon.truncation_tail_l1));
  REQUIRE(!log.has_error());  // warning, not error

  std::ostringstream os;
  log.emit(os);
  const std::string out = os.str();
  REQUIRE(out.find("request_id=eval-") != std::string::npos);
  REQUIRE(out.find("VERDICT-ACCEPT") != std::string::npos);
  REQUIRE(out.find("VERDICT-INCONCLUSIVE") != std::string::npos);
  REQUIRE(out.find("state{") != std::string::npos);
}

int main() { return chebtest::run_all(); }
