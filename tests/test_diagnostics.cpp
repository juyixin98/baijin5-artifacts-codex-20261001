#include "test_framework.hpp"

#include <sstream>

#include "cheb/config.hpp"
#include "cheb/diagnostics.hpp"

using namespace cheb;

TEST_CASE("diag.request_id_is_stable_and_prefixed") {
  const RequestId a{0xdeadbeefcafe1234ULL};
  CHECK_TRUE(a.str() == "req-deadbeefcafe1234", "request_id_format");
  const RequestId b = RequestId::generate(1);
  const RequestId c = RequestId::generate(1);
  CHECK_TRUE(!b.str().empty() && b.str().rfind("req-", 0) == 0,
             "generated_request_id");
  (void)c;
}

TEST_CASE("diag.redaction_never_leaks_value") {
  const RedactedSample r = redacted_sample(3.14159265);
  CHECK_TRUE(r.fingerprint.find("3.14") == std::string::npos,
             "no_decimal_leak");
  CHECK_TRUE(r.magnitude_bucket >= 3.0 && r.magnitude_bucket < 4.0,
             "magnitude_bucket");
  // Deterministic fingerprint for the same bit pattern.
  const RedactedSample r2 = redacted_sample(3.14159265);
  CHECK_TRUE(r.fingerprint == r2.fingerprint, "fingerprint_stable");
  // Different values -> different fingerprints.
  const RedactedSample r3 = redacted_sample(2.71828182);
  CHECK_TRUE(r.fingerprint != r3.fingerprint, "fingerprint_distinct");
}

TEST_CASE("diag.logger_emits_verdict_with_request_id_and_state") {
  std::ostringstream os;
  Logger log(os);
  ErrorReport rep;
  rep.verdict = Verdict::Rejected;
  rep.reason = FailureReason::ResidualExceedsTolerance;
  rep.fit_residual = 1.5e-3;
  rep.truncation_residual = 7.2e-3;
  rep.residual_point_count = 401;
  rep.tail.kind = TailKind::Algebraic;
  rep.tail.value = 9.0e-4;
  rep.explanation = "demo; samples look secret";
  log.decision("req-0000000000000001", rep, 64, 16);
  const std::string out = os.str();
  CHECK_TRUE(out.find("req-0000000000000001") != std::string::npos,
             "log_has_request_id");
  CHECK_TRUE(out.find("\"verdict\":\"rejected\"") != std::string::npos,
             "log_has_verdict");
  CHECK_TRUE(out.find("residual_exceeds_tolerance") != std::string::npos,
             "log_has_reason");
  CHECK_TRUE(out.find("\"fit_residual\":0.0015") != std::string::npos,
             "log_has_fit_residual");
  CHECK_TRUE(out.find("\"truncation_residual\":0.0071999999999999998") !=
                 std::string::npos,
             "log_has_truncation_residual");
}

TEST_CASE("diag.config_profile_loaded") {
  const auto p =
      load_profile("config/tolerance_strict.conf");
  CHECK_NEAR(p.tolerance, 1e-12, 0.0, "config_tolerance");
  CHECK_TRUE(p.smoothness_asserted == false, "config_smoothness");
  const auto p2 =
      load_profile("config/tolerance_smooth.conf");
  CHECK_TRUE(p2.smoothness_asserted == true, "config_smoothness_asserted");
}

int main() { return chebtest::run_all(); }
