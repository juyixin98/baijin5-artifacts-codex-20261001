// End-to-end product/remainder-tree evaluation: duplicate points never divide
// by zero and keep order, the zero polynomial evaluates to zero everywhere, and
// changing the memory-induced batch size never changes a result. Every value is
// asserted against the independent pointwise Horner reference.
#include "harness/test.h"
#include "harness/reference.h"
#include "core/evaluate.h"
#include "core/memory.h"
#include "explain/trace.h"
#include "numcontract/config.h"
#include "numcontract/parse.h"
#include <random>

using namespace mp;
using boost::multiprecision::cpp_int;

namespace {
explain::JobReport run_job(const contract::Job& job, uint64_t mem,
                           uint64_t cap_override = 0) {
  contract::Config cfg;
  cfg.memory_limit_bytes = mem;
  cfg.bytes_per_slot = 24;
  cfg.memory_fudge = 2.0;
  cfg.max_batch_points = cap_override;
  explain::Trace trace;
  return core::evaluate_job(job, cfg, "req-test", trace);
}

contract::Job make_int_job(std::vector<std::string> coeff,
                           std::vector<std::string> pts) {
  contract::Job j;
  j.id = "j";
  j.domain = contract::Domain::Integer;
  j.coeff_tokens = std::move(coeff);
  j.has_coeff_line = true;
  j.point_tokens = std::move(pts);
  j.has_points_line = true;
  return j;
}
contract::Job make_field_job(uint64_t mod, std::vector<std::string> coeff,
                             std::vector<std::string> pts) {
  contract::Job j;
  j.id = "j";
  j.domain = contract::Domain::Field;
  j.modulus = mod;
  j.coeff_tokens = std::move(coeff);
  j.has_coeff_line = true;
  j.point_tokens = std::move(pts);
  j.has_points_line = true;
  return j;
}
} // namespace

MP_TEST(duplicate_integer_points_no_division_error_and_ordered) {
  // f(x) = (x-1)(x-2)(x-3)
  auto job = make_int_job({"1", "-6", "11", "-6"},
                          {"1", "2", "3", "2", "1", "0", "5"});
  auto rep = run_job(job, 64ull * 1024 * 1024);
  MP_CHECK(!rep.failure);
  MP_CHECK(rep.uncertainties.empty());
  MP_CHECK_EQ(rep.results.size(), static_cast<size_t>(7));
  for (size_t i = 0; i < rep.results.size(); ++i)
    MP_CHECK_EQ(rep.results[i].index, i);
  const char* expect[] = {"0", "0", "0", "0", "0", "-6", "24"};
  for (size_t i = 0; i < 7; ++i)
    MP_CHECK_EQ(rep.results[i].value, std::string(expect[i]));
}

MP_TEST(duplicate_field_points_never_trigger_zero_divisor) {
  auto job = make_field_job(1000003, {"1", "-6", "11", "-6"},
                            {"1", "1", "1", "1000004", "2", "2"});
  auto rep = run_job(job, 64ull * 1024 * 1024);
  MP_CHECK(!rep.failure);
  MP_CHECK(rep.uncertainties.empty());
  for (const auto& p : rep.results) MP_CHECK_EQ(p.value, std::string("0"));
}

MP_TEST(zero_polynomial_is_zero_at_every_point_both_domains) {
  auto ji = make_int_job({"0", "0", "0"}, {"-1", "0", "1", "123456789"});
  auto ri = run_job(ji, 64ull * 1024 * 1024);
  MP_CHECK(!ri.failure);
  for (const auto& p : ri.results) MP_CHECK_EQ(p.value, std::string("0"));

  auto jf = make_field_job(97, {"0"}, {"0", "1", "96", "500"});
  auto rf = run_job(jf, 64ull * 1024 * 1024);
  MP_CHECK(!rf.failure);
  for (const auto& p : rf.results) MP_CHECK_EQ(p.value, std::string("0"));
}

MP_TEST(batch_size_changes_preserve_results_integer) {
  // A degree-30 polynomial evaluated at 40 points; compare cap 1 (per-point
  // batches), 3, 7, and unbounded against independent Horner.
  std::vector<std::string> coeff;
  std::mt19937_64 rng(7);
  for (int i = 0; i <= 30; ++i)
    coeff.push_back(std::to_string(static_cast<int64_t>(rng() % 1000) - 500));
  std::vector<std::string> pts;
  for (int i = 0; i < 40; ++i) pts.push_back(std::to_string(i - 20));

  auto job = make_int_job(coeff, pts);
  std::vector<explain::JobReport> reps;
  for (uint64_t cap : {1, 3, 7, 0}) {
    contract::Config cfg;
    cfg.memory_limit_bytes = 64ull * 1024 * 1024;
    cfg.max_batch_points = cap;
    explain::Trace tr;
    reps.push_back(core::evaluate_job(job, cfg, "r", tr));
  }
  for (const auto& rep : reps) {
    MP_CHECK(!rep.failure);
    MP_CHECK(rep.uncertainties.empty());
    MP_CHECK_EQ(rep.results.size(), pts.size());
  }
  for (size_t i = 0; i < pts.size(); ++i) {
    std::string expect = mptest::ref::horner_integer(coeff, pts[i]);
    for (const auto& rep : reps)
      MP_CHECK_EQ(rep.results[i].value, expect);
  }
  // cap=1 must actually force 40 batches, proving the batching path ran.
  MP_CHECK_EQ(reps[0].batches, static_cast<size_t>(40));
  MP_CHECK_EQ(reps[0].batch_cap, static_cast<size_t>(1));
}

MP_TEST(batch_size_changes_preserve_results_field) {
  std::vector<std::string> coeff;
  std::mt19937_64 rng(11);
  const uint64_t mod = 1000003;
  for (int i = 0; i <= 25; ++i)
    coeff.push_back(std::to_string(rng() % mod));
  std::vector<std::string> pts;
  for (int i = 0; i < 37; ++i) pts.push_back(std::to_string((rng() % (2*mod))));

  auto job = make_field_job(mod, coeff, pts);
  std::vector<std::string> baseline;
  for (uint64_t cap : {1, 5, 0}) {
    contract::Config cfg;
    cfg.memory_limit_bytes = 64ull * 1024 * 1024;
    cfg.max_batch_points = cap;
    explain::Trace tr;
    auto rep = core::evaluate_job(job, cfg, "r", tr);
    MP_CHECK(!rep.failure);
    MP_CHECK(rep.uncertainties.empty());
    std::vector<std::string> got;
    for (size_t i = 0; i < pts.size(); ++i) {
      std::string expect = mptest::ref::horner_field(coeff, pts[i], mod);
      MP_CHECK_EQ(rep.results[i].value, expect);
      got.push_back(rep.results[i].value);
    }
    if (baseline.empty()) baseline = got;
    else MP_CHECK(got == baseline);
  }
}

MP_TEST(large_exact_integer_coefficients_grow_without_overflow) {
  // f(x) = (x + 10^40)(x - 10^40) = x^2 - 10^80
  auto job = make_int_job({"1", "0", "-100000000000000000000000000000000000000000000000000000000000000000000000000000000"},
                          {"10000000000000000000000000000000000000000", "1", "2"});
  auto rep = run_job(job, 64ull * 1024 * 1024);
  MP_CHECK(!rep.failure);
  MP_CHECK(rep.uncertainties.empty());
  MP_CHECK_EQ(rep.results[0].value, std::string("0"));
  std::string e1 = mptest::ref::horner_integer(job.coeff_tokens, "1");
  std::string e2 = mptest::ref::horner_integer(job.coeff_tokens, "2");
  MP_CHECK_EQ(rep.results[1].value, e1);
  MP_CHECK_EQ(rep.results[2].value, e2);
}

MP_TEST(planner_splits_to_fit_memory_and_reports_infeasible) {
  contract::Config cfg;
  cfg.memory_limit_bytes = 100000;
  cfg.bytes_per_slot = 24;
  cfg.memory_fudge = 2.0;
  auto plan = core::plan_batches(100, cfg);
  MP_CHECK(plan.feasible);
  MP_CHECK(plan.max_batch_size < 100);
  size_t total = 0;
  for (size_t s : plan.sizes) {
    MP_CHECK(s <= plan.max_batch_size);
    total += s;
  }
  MP_CHECK_EQ(total, static_cast<size_t>(100));

  contract::Config tiny = cfg;
  tiny.memory_limit_bytes = 1;
  auto bad = core::plan_batches(10, tiny);
  MP_CHECK(!bad.feasible);
}
