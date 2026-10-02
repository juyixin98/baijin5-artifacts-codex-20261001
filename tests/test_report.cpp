// Interpretability tests: request/job identity correlation, definite results
// vs failures vs uncertainties kept separate, and machine-readable JSON.
#include "harness/test.h"
#include "core/evaluate.h"
#include "explain/report.h"
#include "explain/trace.h"
#include "numcontract/parse.h"

using namespace mp;

namespace {
contract::Job job_int(const std::string& id,
                      std::vector<std::string> c,
                      std::vector<std::string> p) {
  contract::Job j;
  j.id = id;
  j.domain = contract::Domain::Integer;
  j.coeff_tokens = std::move(c);
  j.has_coeff_line = true;
  j.point_tokens = std::move(p);
  j.has_points_line = true;
  return j;
}
} // namespace

MP_TEST(text_report_separates_sections_and_correlates_identity) {
  explain::Trace trace;
  auto rep = core::evaluate_job(job_int("job-A", {"1", "0"}, {"3", "3"}),
                                contract::Config{}, "req-77", trace);
  explain::RequestReport rr;
  rr.request_id = "req-77";
  rr.version = "test";
  rr.jobs.push_back(std::move(rep));
  rr.trace = &trace;
  MP_CHECK(!rr.trace->entries().empty());
  for (const auto& e : rr.trace->entries()) {
    MP_CHECK_EQ(e.request_id, std::string("req-77"));
    MP_CHECK(!e.position.empty());
    MP_CHECK(!e.step.empty());
  }
  std::string txt = explain::render_text(rr, true);
  MP_CHECK(txt.find("request=req-77") != std::string::npos);
  MP_CHECK(txt.find("id=job-A") != std::string::npos);
  MP_CHECK(txt.find("STEP ") != std::string::npos);
  MP_CHECK(txt.find("RESULT idx=0") != std::string::npos);
  MP_CHECK(txt.find("FAILURE") == std::string::npos);
  MP_CHECK(txt.find("UNCERTAIN") == std::string::npos);
}

MP_TEST(json_report_marks_failure_category_and_keeps_results_empty) {
  explain::Trace trace;
  contract::Job j = job_int("bad", {"1"}, {"1"});
  j.domain = contract::Domain::Field; // claims FIELD but has no modulus
  contract::Config cfg;
  auto rep = core::evaluate_job(j, cfg, "req-json", trace);
  MP_CHECK(static_cast<bool>(rep.failure));
  MP_CHECK(rep.results.empty());
  explain::RequestReport rr;
  rr.request_id = "req-json";
  rr.version = "test";
  rr.jobs.push_back(std::move(rep));
  std::string js = explain::render_json(rr, false);
  MP_CHECK(js.find("\"code\": \"DOMAIN_MISMATCH\"") != std::string::npos);
  MP_CHECK(js.find("\"outcome\": \"FAILURE\"") != std::string::npos);
  MP_CHECK(js.find("\"request\": \"req-json\"") != std::string::npos);
}

MP_TEST(parser_failure_categories_reach_request_level_report) {
  auto parsed = contract::parse_request(
      "REQUEST q\nJOB x\nDOMAIN INTEGER\nMOD 5\nCOEFF 1\nPOINTS 1\n");
  MP_CHECK(!parsed.failures.empty());
  bool domain_seen = false, id_ok = false;
  for (const auto& f : parsed.failures) {
    if (f.code == contract::Fail::DomainMismatch) domain_seen = true;
    if (f.request_id == "q") id_ok = true;
  }
  MP_CHECK(domain_seen);
  MP_CHECK(id_ok);
}
