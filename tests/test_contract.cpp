// Tests for the numeric contract: grammar, failure categories, primality,
// domain isolation, config parsing and per-job validation.
#include "harness/test.h"
#include "numcontract/config.h"
#include "numcontract/parse.h"
#include "numcontract/primality.h"

using namespace mp::contract;

MP_TEST(primality_classifies_exact_cases) {
  MP_CHECK(is_prime(2));
  MP_CHECK(is_prime(3));
  MP_CHECK(is_prime(97));
  MP_CHECK(is_prime(1000003));
  MP_CHECK(is_prime(2305843009213693951ull)); // Mersenne 2^61-1
  MP_CHECK(!is_prime(0));
  MP_CHECK(!is_prime(1));
  MP_CHECK(!is_prime(4));
  MP_CHECK(!is_prime(1000002));
  MP_CHECK(!is_prime(3215031751ull));   // known strong-pseudoprime composite
}

MP_TEST(valid_integer_and_field_requests_parse) {
  auto r = parse_request(
      "REQUEST r1\n"
      "JOB a\nDOMAIN INTEGER\nCOEFF 1 2 3\nPOINTS 0 1 2\n"
      "JOB b\nDOMAIN FIELD\nMOD 17\nCOEFF 1 0 -1\nPOINTS 4 5\n");
  MP_CHECK(r.ok());
  MP_CHECK_EQ(r.request.id, std::string("r1"));
  MP_CHECK_EQ(r.request.jobs.size(), static_cast<size_t>(2));
  MP_CHECK(r.request.jobs[0].domain == Domain::Integer);
  MP_CHECK(r.request.jobs[1].domain == Domain::Field);
  MP_CHECK_EQ(*r.request.jobs[1].modulus, static_cast<uint64_t>(17));
}

MP_TEST(duplicate_points_are_kept_in_order) {
  auto r = parse_request("JOB a\nDOMAIN INTEGER\nCOEFF 1 1\nPOINTS 5 5 2 5\n");
  MP_CHECK(r.ok());
  const auto& pts = r.request.jobs[0].point_tokens;
  MP_CHECK_EQ(pts.size(), static_cast<size_t>(4));
  MP_CHECK_EQ(pts[0], std::string("5"));
  MP_CHECK_EQ(pts[1], std::string("5"));
  MP_CHECK_EQ(pts[2], std::string("2"));
  MP_CHECK_EQ(pts[3], std::string("5"));
}

namespace {
bool has_code(const std::vector<Failure>& fs, Fail c) {
  for (const auto& f : fs) if (f.code == c) return true;
  return false;
}
Failure validate_first(const std::string& text, const Config& cfg = Config{}) {
  auto r = parse_request(text);
  if (!r.failures.empty()) return r.failures.front();
  if (r.request.jobs.empty()) return {Fail::MalformedRequest, "no job", "", ""};
  return validate_job(r.request.jobs.front(), cfg);
}
} // namespace

MP_TEST(duplicate_job_id_is_rejected) {
  auto r = parse_request(
      "JOB a\nDOMAIN INTEGER\nCOEFF 1\nPOINTS 1\n"
      "JOB a\nDOMAIN INTEGER\nCOEFF 2\nPOINTS 2\n");
  MP_CHECK(has_code(r.failures, Fail::DuplicateJobId));
}

MP_TEST(domain_and_modulus_mixing_rejected) {
  MP_CHECK(validate_first("JOB a\nDOMAIN INTEGER\nMOD 17\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::DomainMismatch);
  MP_CHECK(validate_first("JOB a\nDOMAIN FIELD\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::InvalidModulus);
  MP_CHECK(validate_first("JOB a\nDOMAIN FIELD\nMOD 12\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::NonPrimeModulus);
  // 2^63 fits uint64 but violates the field representation ceiling.
  MP_CHECK(validate_first(
               "JOB a\nDOMAIN FIELD\nMOD 9223372036854775808\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::ModulusTooLarge);
  // A literal beyond uint64 is rejected at parse time as an invalid modulus.
  MP_CHECK(validate_first(
               "JOB a\nDOMAIN FIELD\nMOD 18446744073709551616\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::InvalidModulus);
  MP_CHECK(validate_first(
               "JOB a\nDOMAIN FIELD\nMOD 1000000007\nCOEFF 1\nPOINTS 1\n")
               .code == Fail::None);
  MP_CHECK(validate_first("JOB a\nDOMAIN FIELD\nMOD 1000000007\nCOEFF 1\nPOINTS 1\n",
                          []{ Config c; c.memory_limit_bytes = 1; return c; }())
               .code == Fail::InfeasibleBatchLimit);
}

MP_TEST(coefficient_and_point_literal_failures_are_distinct) {
  MP_CHECK(validate_first("JOB a\nDOMAIN INTEGER\nCOEFF 1 x 3\nPOINTS 1\n")
               .code == Fail::MalformedCoefficient);
  MP_CHECK(validate_first("JOB a\nDOMAIN INTEGER\nCOEFF 1\nPOINTS 1 y\n")
               .code == Fail::MalformedPoint);
  MP_CHECK(validate_first("JOB a\nDOMAIN INTEGER\nPOINTS 1\n")
               .code == Fail::EmptyCoefficients);
}

MP_TEST(config_size_parsing_and_unknown_keys) {
  Config c;
  auto errs = parse_config(
      "memory_limit = 256KiB\nbytes_per_slot = 16\nmemory_fudge = 2\n", c);
  MP_CHECK(errs.empty());
  MP_CHECK_EQ(c.memory_limit_bytes, static_cast<uint64_t>(256 * 1024));
  MP_CHECK_EQ(c.bytes_per_slot, 16.0);
  errs = parse_config("bogus_key = 1\n", c);
  MP_CHECK(has_code(errs, Fail::MalformedRequest));
  uint64_t v = 0;
  MP_CHECK(parse_size_bytes("2MiB", v) && v == 2ull * 1024 * 1024);
  MP_CHECK(parse_size_bytes("3000", v) && v == 3000);
  MP_CHECK(!parse_size_bytes("nonsense", v));
}
