// Acceptance: results/logs are correlatable to a request and name version
// and processing location. Failure and uncertainty reasons are separate
// fields from success details.
#include "common/request.hpp"
#include "contract/numeric_contract.hpp"
#include "service/response.hpp"
#include "test_framework.hpp"
#include <sstream>

int main() {
  tf::begin("request ids are unique and non-empty");
  {
    auto a = fft::common::RequestContext::generateId();
    auto b = fft::common::RequestContext::generateId();
    tf::check(!a.empty() && !b.empty() && a != b, "ids");
  }

  tf::begin("context carries version and location metadata");
  {
    fft::common::RequestContext ctx{"req-xyz", "mathcore.bluestein",
                                   BLUESTEIN_FFT_VERSION_STRING,
                                   "bluestein:L=64"};
    tf::check(ctx.requestId == "req-xyz", "id propagation");
    tf::check(ctx.version == BLUESTEIN_FFT_VERSION_STRING, "version");
    tf::check(ctx.location == "bluestein:L=64", "location");
  }

  tf::begin("response separates failure reason from success detail");
  {
    fft::service::Response ok;
    ok.requestId = "req-1";
    ok.detail = "maxAbs=1e-12";
    tf::check(ok.failureReason.empty(), "success has no failure reason");

    fft::service::Response bad;
    bad.requestId = "req-2";
    bad.status = fft::common::FftStatus::ReferenceMismatch;
    bad.failureReason = "bin 4 off by 1.0";
    tf::check(!bad.failureReason.empty(), "failure reason present");
  }

  tf::begin("contract verdict flags uncertainty distinctly");
  {
    fft::contract::ErrorMetrics m;
    m.n = 10; m.maxAbsError = 5e-6; m.rmsAbsError = 1e-6; m.worstBin = 3;
    auto v = fft::contract::judge(m, {1e-8, 1e-4, 1e-2});
    tf::check(v.category == fft::common::FftStatus::PrecisionRisk,
              std::string("expected precision risk, got ") +
                  fft::common::statusName(v.category));
    tf::check(v.uncertain, "must be marked uncertain");
    tf::check(!v.failureReason.empty(), "uncertainty reason listed");
  }
  return tf::finish();
}
