#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "framework.hpp"
#include "polyeval/config.hpp"
#include "polyeval/contract.hpp"
#include "polyeval/driver.hpp"
#include "polyeval/explainer.hpp"
#include "polyeval/kernel.hpp"
#include "polyeval/log.hpp"

using namespace polyeval;
using B = contract::Big;
using F = field::ModInt;

// ---- Independent, point-wise Horner references (not built on the kernel) ----
static B horner_big(const std::vector<B>& c, const B& x) {
  B r(0);
  for (auto it = c.rbegin(); it != c.rend(); ++it) r = r * x + *it;
  return r;
}
static field::u64 horner_mod(const std::vector<field::u64>& c, field::u64 x, field::u64 p) {
  field::u128 r = 0;
  for (auto it = c.rbegin(); it != c.rend(); ++it)
    r = (r * x + *it) % p;
  return static_cast<field::u64>(r);
}

static std::vector<B> eval_exact_direct(const contract::ParsedRequest& r) {
  std::vector<B> out;
  for (const B& x : r.points) out.push_back(horner_big(r.coeff, x));
  return out;
}

static log::JsonlLogger null_log() { return log::JsonlLogger(""); }

// ============================= kernel: exact =============================
TEST_CASE(exact_basic_concrete_values) {
  // P(X)=1+2X+3X^2 ; expected values computed by hand/Horner independently.
  kernel::Poly<B> p;
  p.coeff = {B(1), B(2), B(3)};
  std::vector<B> pts = {B(0), B(1), B(2), B(3), B(-2)};
  auto got = kernel::eval_batch(p, pts);
  const std::vector<std::string> want = {"1", "6", "17", "34", "9"};
  pe_test::check(got.size() == want.size(), "size");
  for (std::size_t i = 0; i < want.size(); ++i)
    pe_test::expect_str(got[i].str(), want[i], "exact_basic point " + std::to_string(i));
}

TEST_CASE(exact_duplicate_points_keep_order_no_divzero) {
  kernel::Poly<B> p;
  p.coeff = {B(5), B(0), B(1)};  // X^2 + 5
  std::vector<B> pts = {B(4), B(4), B(4), B(-1), B(4)};
  auto got = kernel::eval_batch(p, pts);
  for (std::size_t i = 0; i < pts.size(); ++i) {
    pe_test::expect_str(got[i].str(), horner_big(p.coeff, pts[i]).str(),
                        "dup point " + std::to_string(i));
  }
  pe_test::expect_str(got[0].str(), got[4].str(), "duplicates equal");
  pe_test::expect_str(got[3].str(), "6", "x=-1 value");
}

TEST_CASE(exact_zero_polynomial) {
  kernel::Poly<B> p;
  p.coeff = {B(0), B(0), B(0)};
  std::vector<B> pts = {B(0), B(7), B(-7), B(123456789)};
  auto got = kernel::eval_batch(p, pts);
  for (std::size_t i = 0; i < pts.size(); ++i)
    pe_test::expect_str(got[i].str(), "0", "zero poly at " + std::to_string(i));
}

TEST_CASE(exact_constant_polynomial) {
  kernel::Poly<B> p;
  p.coeff = {B(42)};
  std::vector<B> pts = {B(0), B(-1), B(99)};
  auto got = kernel::eval_batch(p, pts);
  for (auto& v : got) pe_test::expect_str(v.str(), "42", "constant");
}

TEST_CASE(exact_single_point) {
  kernel::Poly<B> p;
  p.coeff = {B(1), B(1)};
  auto got = kernel::eval_batch(p, {B(10)});
  pe_test::expect_str(got[0].str(), "11", "single");
}

TEST_CASE(exact_non_power_of_two_sizes) {
  kernel::Poly<B> p;
  p.coeff = {B(3), B(-2), B(1), B(7)};
  for (std::size_t n : {1u, 2u, 3u, 5u, 6u, 7u, 9u, 13u, 31u, 33u, 100u}) {
    std::vector<B> pts(n);
    for (std::size_t i = 0; i < n; ++i) pts[i] = B(static_cast<long long>(i) - 50);
    auto got = kernel::eval_batch(p, pts);
    for (std::size_t i = 0; i < n; ++i)
      pe_test::expect_str(got[i].str(), horner_big(p.coeff, pts[i]).str(),
                          "n=" + std::to_string(n) + " i=" + std::to_string(i));
  }
}

TEST_CASE(exact_randomised_vs_independent_horner) {
  std::mt19937_64 rng(20261003);
  for (int trial = 0; trial < 40; ++trial) {
    std::size_t deg = rng() % 12;
    std::size_t n = 1 + rng() % 64;
    kernel::Poly<B> p;
    for (std::size_t k = 0; k <= deg; ++k)
      p.coeff.push_back(B(static_cast<long long>(rng() % 2001) - 1000));
    std::vector<B> pts(n);
    for (std::size_t i = 0; i < n; ++i) {
      pts[i] = B(static_cast<long long>(rng() % 201) - 100);
      if (rng() % 4 == 0 && i > 0) pts[i] = pts[rng() % i];  // inject duplicates
    }
    auto got = kernel::eval_batch(p, pts);
    for (std::size_t i = 0; i < n; ++i)
      pe_test::expect_str(got[i].str(), horner_big(p.coeff, pts[i]).str(),
                          "random trial " + std::to_string(trial));
  }
}

TEST_CASE(exact_large_integers_exact) {
  kernel::Poly<B> p;
  p.coeff = {B("100000000000000000000"), B("200000000000000000000"), B(1)};
  std::vector<B> pts = {B("1000000000"), B("-1000000000")};
  auto got = kernel::eval_batch(p, pts);
  // P(x)=x^2 + 2e20 x + 1e20 ; x=+-1e9 -> x^2=1e18 ; 2e20*x = +-2e29
  pe_test::expect_str(got[0].str(), "200000000101000000000000000000", "big +");
  pe_test::expect_str(got[1].str(), "-199999999899000000000000000000", "big -");
}

// ============================= kernel: field =============================
static std::vector<F> run_field(const std::vector<field::u64>& c,
                                const std::vector<field::u64>& x, field::u64 p) {
  field::ModIntScope sc(p);
  kernel::Poly<F> poly;
  for (auto v : c) poly.coeff.push_back(F::raw(v));
  std::vector<F> pts;
  for (auto v : x) pts.push_back(F::raw(v));
  return kernel::eval_batch(poly, pts);
}

TEST_CASE(field_basic_concrete_residue) {
  // mod 17, P=1+2X+3X^2
  auto got = run_field({1, 2, 3}, {0, 1, 2, 3, 16}, 17);
  const std::vector<field::u64> want = {1, 6, 0, 0, 9};  // 17->0, 34->0, 769? x=16:1+32+768=801 mod17=2? verify below
  // Compute independently and assert exactly; the static list above is only a
  // guard for the first three well-known points.
  pe_test::expect_eq(got[0].v, field::u64(1), "f x=0");
  pe_test::expect_eq(got[1].v, field::u64(6), "f x=1");
  pe_test::expect_eq(got[2].v, field::u64(0), "f x=2 (17=0)");
  pe_test::expect_eq(got[3].v, field::u64(0), "f x=3 (34=0)");
  for (std::size_t i = 0; i < got.size(); ++i) {
    field::u64 w = horner_mod({1, 2, 3}, std::vector<field::u64>{0,1,2,3,16}[i], 17);
    pe_test::expect_eq(got[i].v, w, "field point " + std::to_string(i));
  }
  (void)want;
}

TEST_CASE(field_duplicate_points) {
  auto got = run_field({2, 0, 1}, {5, 5, 5, 1}, 7);  // X^2+2 mod 7
  // x=5: 25+2=27=6 mod7; x=1:3
  pe_test::expect_eq(got[0].v, field::u64(6), "dup0");
  pe_test::expect_eq(got[1].v, field::u64(6), "dup1");
  pe_test::expect_eq(got[2].v, field::u64(6), "dup2");
  pe_test::expect_eq(got[3].v, field::u64(3), "x=1");
}

TEST_CASE(field_random_prime_and_points) {
  const field::u64 p = 1000000007;
  pe_test::check(field::is_prime_u64(p), "1e9+7 prime");
  std::mt19937_64 rng(42);
  std::vector<field::u64> c(9), x(128);
  for (auto& v : c) v = rng() % p;
  for (auto& v : x) { v = rng() % p; }
  auto got = run_field(c, x, p);
  for (std::size_t i = 0; i < x.size(); ++i)
    pe_test::expect_eq(got[i].v, horner_mod(c, x[i], p),
                       "field random " + std::to_string(i));
}

TEST_CASE(field_wrapping_is_residue) {
  // Coefficients/points canonical but integer result exceeds p; residue exact.
  auto got = run_field({0, 0, 1}, {1000000}, 1000000007);
  field::u64 w = horner_mod({0, 0, 1}, 1000000, 1000000007);
  pe_test::expect_eq(got[0].v, w, "wrapped residue");
  pe_test::check(got[0].v < 1000000007, "canonical range");
}

TEST_CASE(primality_check_exact) {
  pe_test::check(field::is_prime_u64(2), "2 prime");
  pe_test::check(field::is_prime_u64(3), "3 prime");
  pe_test::check(!field::is_prime_u64(1), "1 not prime");
  pe_test::check(!field::is_prime_u64(4), "4 not prime");
  pe_test::check(!field::is_prime_u64(1000000007ULL - 1), "1e9+6 composite");
  pe_test::check(field::is_prime_u64(18446744073709551557ULL), "largest 64bit prime");
  pe_test::check(!field::is_prime_u64(18446744073709551558ULL), "even composite");
}

// ============================= product-tree slots / batching =============================
TEST_CASE(product_tree_slot_counts) {
  pe_test::expect_eq(kernel::product_tree_slots(1), field::u64(2), "n=1 slots");
  // slots = n*levels + total_nodes.
  pe_test::expect_eq(kernel::product_tree_slots(2), field::u64(2 * 2 + (2 + 1)), "n=2");
  // n=3: levels=3; nodes=3+2+1=6; spans per level: leaf 3, next 3, root 3 => n*L=9
  pe_test::expect_eq(kernel::product_tree_slots(3), field::u64(3 * 3 + 6), "n=3 slots");
  pe_test::expect_eq(kernel::product_tree_slots(3), field::u64(15), "n=3 ==15");
  pe_test::check(kernel::product_tree_slots(64) > kernel::product_tree_slots(32),
                 "monotonic slots");
}

TEST_CASE(choose_batch_respects_budget) {
  // 8-byte scalars; batch=1 needs 2*8=16 bytes.
  pe_test::expect_eq(kernel::choose_batch_size(100, 15, 8), std::size_t(0), "too small ->0");
  std::size_t b = kernel::choose_batch_size(100, 64, 8);  // fits up to slots<=8
  pe_test::check(b >= 1 && kernel::product_tree_slots(b) * 8 <= 64, "budget holds");
  pe_test::check(kernel::product_tree_slots(b + 1) * 8 > 64 || b == 100, "maximal b");
  std::size_t big = kernel::choose_batch_size(100, 1ull << 40, 8);
  pe_test::expect_eq(big, std::size_t(100), "generous budget -> n");
}

TEST_CASE(batched_results_identical_to_unbatched) {
  field::ModIntScope sc(104729);
  std::mt19937_64 rng(7);
  const std::size_t n = 200;
  kernel::Poly<F> poly;
  for (int k = 0; k < 6; ++k) poly.coeff.push_back(F::raw(rng() % 104729));
  std::vector<F> pts(n);
  for (std::size_t i = 0; i < n; ++i) pts[i] = F::raw(rng() % 104729);

  std::vector<F> full = kernel::eval_batch(poly, pts);
  for (std::size_t batch : {1u, 2u, 3u, 7u, 31u, 100u, 200u}) {
    std::vector<F> stitched(n);
    for (std::size_t s = 0; s < n; s += batch) {
      std::size_t e = std::min(s + batch, n);
      std::vector<F> chunk(pts.begin() + s, pts.begin() + e);
      auto v = kernel::eval_batch(poly, chunk);
      for (std::size_t i = 0; i < v.size(); ++i) stitched[s + i] = v[i];
    }
    for (std::size_t i = 0; i < n; ++i)
      pe_test::expect_eq(stitched[i].v, full[i].v,
                         "batch " + std::to_string(batch) + " idx " + std::to_string(i));
  }
}

// ============================= contract / parser =============================
static contract::ParsedRequest one_ok(const std::string& text) {
  auto o = contract::parse_requests(text);
  pe_test::check(o.failures.empty(),
                 o.failures.empty() ? "" : ("unexpected failure: " + o.failures[0].message));
  pe_test::check(o.requests.size() == 1, "exactly one request");
  return o.requests[0];
}

TEST_CASE(parse_exact_request) {
  auto r = one_ok("request: a\nmode: exact\ncoeff: 1 -2 3\npoints: 0 1 2\n");
  pe_test::expect_str(r.id, "a", "id");
  pe_test::check(r.mode == contract::Mode::Exact, "exact mode");
  pe_test::expect_eq(r.coeff.size(), std::size_t(3), "3 coeff");
  pe_test::expect_str(r.coeff[1].str(), "-2", "negative coeff");
}

TEST_CASE(parse_field_request_canonical) {
  auto r = one_ok("request: f\nmode: field\nprime: 17\ncoeff: 1 2 3\npoints: 0 16\n");
  pe_test::check(r.prime && *r.prime == 17, "prime 17");
  pe_test::expect_eq(r.points.size(), std::size_t(2), "2 points");
}

TEST_CASE(parse_rejects_mixed_mode_prefix) {
  auto o = contract::parse_requests(
      "request: m\nmode: exact\ncoeff: 1 2\npoints: F:3\n");
  pe_test::check(o.requests.empty(), "no accepted request");
  pe_test::check(o.failures.size() == 1, "one failure");
  pe_test::check(o.failures[0].code == contract::FailCode::MixedMode,
                 contract::fail_code_name(o.failures[0].code));
}

TEST_CASE(parse_rejects_field_with_signed) {
  auto o = contract::parse_requests(
      "request: g\nmode: field\nprime: 17\ncoeff: 1 -2\npoints: 3\n");
  pe_test::check(o.requests.empty(), "rejected");
  pe_test::check(o.failures[0].code == contract::FailCode::BadFieldElement,
                 "bad field element category");
}

TEST_CASE(parse_rejects_out_of_range_residue) {
  auto o = contract::parse_requests(
      "request: g\nmode: field\nprime: 17\ncoeff: 1 2\npoints: 17\n");
  pe_test::check(o.failures[0].code == contract::FailCode::BadFieldElement, "17 not canonical");
}

TEST_CASE(parse_rejects_non_prime) {
  auto o = contract::parse_requests(
      "request: g\nmode: field\nprime: 100\ncoeff: 1\npoints: 2\n");
  pe_test::check(o.failures[0].code == contract::FailCode::NonPrimeModulus,
                 "composite modulus");
}

TEST_CASE(parse_rejects_empty_points) {
  auto o = contract::parse_requests("request: e\nmode: exact\ncoeff: 1\npoints:\n");
  pe_test::check(o.failures[0].code == contract::FailCode::EmptyPoints, "empty points");
}

TEST_CASE(parse_rejects_missing_mode_and_coeff) {
  auto o = contract::parse_requests("request: e\npoints: 1 2\n");
  pe_test::check(o.failures[0].code == contract::FailCode::MissingField, "missing mode");
}

TEST_CASE(parse_unknown_key_is_parse_error) {
  auto o = contract::parse_requests("request: e\nmode: exact\nfoo: 1\ncoeff:1\npoints:1\n");
  pe_test::check(o.failures[0].code == contract::FailCode::ParseError, "unknown key");
}

TEST_CASE(parse_bad_number_literal) {
  auto o = contract::parse_requests("request: e\nmode: exact\ncoeff: 1 abc\npoints: 1\n");
  pe_test::check(o.failures[0].code == contract::FailCode::ParseError, "literal");
}

TEST_CASE(parse_multiple_requests_isolated_failures_preserve_order) {
  std::string text =
      "request: good1\nmode: exact\ncoeff: 1 1\npoints: 2\n\n"
      "request: bad\nmode: field\nprime: 9\ncoeff: 1\npoints: 1\n\n"
      "request: good2\nmode: exact\ncoeff: 0 1\npoints: 5 5\n";
  auto o = contract::parse_requests(text);
  pe_test::expect_eq(o.requests.size(), std::size_t(2), "two good");
  pe_test::expect_str(o.requests[0].id, "good1", "order1");
  pe_test::expect_str(o.requests[1].id, "good2", "order2");
  pe_test::expect_eq(o.failures.size(), std::size_t(1), "one isolated failure");
  pe_test::expect_str(o.failures[0].id, "bad", "failure id");
  pe_test::check(o.failures[0].code == contract::FailCode::NonPrimeModulus, "bad prime");
}

// ============================= driver end-to-end =============================
static contract::Response response_for(const contract::ParsedRequest& r,
                                       const config::EvalConfig& cfg = {}) {
  auto lg = null_log();
  contract::ParseOutcome po;
  po.requests.push_back(r);
  auto rep = driver::run(po, cfg, lg);
  pe_test::check(rep.responses.size() == 1, "one response");
  return rep.responses[0];
}

TEST_CASE(driver_exact_end_to_end_concrete) {
  contract::ParsedRequest r;
  r.id = "t";
  r.mode = contract::Mode::Exact;
  r.coeff = {B(1), B(2), B(3)};
  r.points = {B(0), B(1), B(2), B(2), B(3)};  // duplicate 2
  auto res = response_for(r);
  pe_test::check(res.status == contract::FailCode::Ok, contract::fail_code_name(res.status));
  pe_test::expect_eq(res.results.size(), std::size_t(5), "5 results");
  pe_test::expect_str(res.results[0].value.str(), "1", "y0");
  pe_test::expect_str(res.results[2].value.str(), "17", "y2");
  pe_test::expect_str(res.results[3].value.str(), "17", "y3 duplicate same");
  pe_test::expect_eq(res.results[2].index, std::size_t(2), "index preserved 2");
  pe_test::expect_eq(res.results[3].index, std::size_t(3), "index preserved 3");
  pe_test::check(res.results[0].exact_crosschecked, "Horner crosscheck flag");
}

TEST_CASE(driver_field_end_to_end) {
  contract::ParsedRequest r;
  r.id = "f";
  r.mode = contract::Mode::Field;
  r.prime = 17;
  r.coeff = {B(1), B(2), B(3)};
  r.points = {B(2), B(3)};
  auto res = response_for(r);
  pe_test::check(res.status == contract::FailCode::Ok, "ok");
  pe_test::expect_str(res.results[0].value.str(), "0", "17 mod17");
  pe_test::expect_str(res.results[1].value.str(), "0", "34 mod17");
}

TEST_CASE(driver_batch_size_change_keeps_results) {
  for (std::uint64_t batch : {1u, 2u, 5u, 100u}) {
    contract::ParsedRequest r;
    r.id = "b"; r.mode = contract::Mode::Exact; r.batch_size = batch;
    r.coeff = {B(4), B(-5), B(1), B(2)};
    for (int i = 0; i < 20; ++i) r.points.push_back(B(i - 10));
    auto res = response_for(r);
    pe_test::check(res.status == contract::FailCode::Ok, "ok batch");
    for (std::size_t i = 0; i < r.points.size(); ++i)
      pe_test::expect_str(res.results[i].value.str(),
                          horner_big(r.coeff, r.points[i]).str(),
                          "batch=" + std::to_string(batch) + " i=" + std::to_string(i));
  }
}

TEST_CASE(driver_memory_budget_batches_but_consistent) {
  config::EvalConfig cfg;
  cfg.memory_bytes = 200;      // tiny; exact scalar hint 32 -> small batches
  cfg.exact_bytes_per_scalar = 32;
  contract::ParsedRequest r;
  r.id = "mem"; r.mode = contract::Mode::Exact;
  r.coeff = {B(1), B(1)};
  for (int i = 0; i < 50; ++i) r.points.push_back(B(i));
  auto res = response_for(r, cfg);
  pe_test::check(res.status == contract::FailCode::Ok, "tiny budget still ok");
  pe_test::check(res.batch_size_used < 50, "batching actually happened");
  for (std::size_t i = 0; i < r.points.size(); ++i)
    pe_test::expect_str(res.results[i].value.str(),
                        horner_big(r.coeff, r.points[i]).str(),
                        "mem batching consistency i=" + std::to_string(i));
}

TEST_CASE(driver_impossible_budget_is_failure_category) {
  config::EvalConfig cfg;
  cfg.memory_bytes = 1;   // below single-point tree (2 slots * 32 bytes)
  contract::ParsedRequest r;
  r.id = "nomem"; r.mode = contract::Mode::Exact; r.coeff = {B(1)}; r.points = {B(1)};
  auto res = response_for(r, cfg);
  pe_test::check(res.status == contract::FailCode::TooSmallMemoryBudget,
                 "specific failure code");
}

TEST_CASE(driver_crosscheck_uncertainty_flagged_separately) {
  config::EvalConfig cfg;
  cfg.crosscheck_bits = 4;  // values > 15 in magnitude -> crosscheck skipped
  contract::ParsedRequest r;
  r.id = "u"; r.mode = contract::Mode::Exact;
  r.coeff = {B(0), B(0), B(100)};  // 100 x^2
  r.points = {B(1), B(2)};         // 100, 400
  auto res = response_for(r, cfg);
  pe_test::check(res.results[0].uncertain, "100 -> uncertain");
  pe_test::check(res.results[1].uncertain, "400 -> uncertain");
  auto e = explainer::explain_point(res.results[0], contract::Mode::Exact, 0);
  pe_test::expect_str(e.status, "UNCERTAIN", "explanation status");
}

TEST_CASE(config_parse_reports_invalid_value) {
  auto c = config::load_config("memory_bytes = notanumber\n");
  pe_test::check(!c.errors.empty(), "config error captured");
  auto c2 = config::load_config("memory_bytes = 4096\nunknown_thing = 1\n");
  pe_test::check(!c2.errors.empty(), "unknown key flagged");
  pe_test::expect_eq(c2.config.memory_bytes, std::uint64_t(4096), "valid value applied");
}

TEST_CASE(driver_complexity_stats_recorded) {
  contract::ParsedRequest r;
  r.id = "c"; r.mode = contract::Mode::Exact;
  r.coeff = {B(1), B(2), B(3)};
  for (int i = 0; i < 32; ++i) r.points.push_back(B(i));
  auto res = response_for(r);
  pe_test::check(res.multiply_scalar_ops > 0, "multiply ops counted");
  pe_test::check(res.remainder_scalar_ops > 0, "remainder ops counted");
  pe_test::check(res.tree_slots > 0, "slots counted");
}

// ============================= runner =============================
int main() {
  int failed = 0;
  for (auto& tc : pe_test::registry()) {
    try {
      tc.fn();
      std::printf("[PASS] %s\n", tc.name.c_str());
    } catch (const pe_test::AssertionError& e) {
      std::printf("[FAIL] %s :: %s\n", tc.name.c_str(), e.msg.c_str());
      ++failed;
    } catch (const std::exception& e) {
      std::printf("[ERROR] %s :: unexpected exception: %s\n", tc.name.c_str(), e.what());
      ++failed;
    }
  }
  std::printf("\n%d/%d cases passed\n",
              static_cast<int>(pe_test::registry().size()) - failed,
              static_cast<int>(pe_test::registry().size()));
  return failed == 0 ? 0 : 1;
}
