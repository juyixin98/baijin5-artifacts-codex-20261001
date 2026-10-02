#include "core/evaluate.h"
#include "core/memory.h"
#include "core/product_tree.h"
#include "core/rings.h"
#include "numcontract/config.h"
#include <sstream>

namespace mp::core {
using contract::Domain;
using contract::Fail;
using contract::Failure;
using contract::Job;
using contract::Config;

namespace {

template <class Ops>
std::vector<typename Ops::D> parse_digits(const Ops& ops,
                                          const std::vector<std::string>& toks,
                                          bool& ok) {
  std::vector<typename Ops::D> out;
  out.reserve(toks.size());
  for (const auto& t : toks) {
    typename Ops::D d;
    if (!ops.parse(t, d)) { ok = false; return {}; }
    out.push_back(d);
  }
  ok = true;
  return out;
}

// coeff tokens arrive highest-degree-first; the kernel stores low-first.
template <class D>
std::vector<D> hi_to_lo_to_low_first(std::vector<D> hi) {
  std::reverse(hi.begin(), hi.end());
  while (!hi.empty()) {
    // caller canonicalises via Ops; this generic helper only reorders, so
    // trailing zeros are removed at the digit-aware call site.
    break;
  }
  return hi;
}

std::string join(const std::vector<size_t>& v) {
  std::ostringstream os;
  for (size_t i = 0; i < v.size(); ++i) os << (i ? "," : "") << v[i];
  return os.str();
}

template <class Ops>
void run_domain(const Ops& ops, const Job& job, const Config& cfg,
                const std::string& request_id, explain::JobReport& rep,
                explain::Trace& trace) {
  bool okc = false, okp = false;
  auto coeff_hi = parse_digits(ops, job.coeff_tokens, okc);
  auto points = parse_digits(ops, job.point_tokens, okp);
  if (!okc || !okp) {
    rep.failure = {Fail::InternalError, "digit conversion failed after validation",
                   "job '" + job.id + "'", request_id};
    return;
  }
  std::reverse(coeff_hi.begin(), coeff_hi.end());
  if constexpr (Ops::kModular) {
    while (!coeff_hi.empty() && coeff_hi.back() == 0) coeff_hi.pop_back();
  } else {
    while (!coeff_hi.empty() && coeff_hi.back() == 0) coeff_hi.pop_back();
  }

  BatchPlan plan = plan_batches(points.size(), cfg);
  rep.batch_cap = plan.max_batch_size;
  rep.batches = plan.sizes.size();
  rep.tree_bytes_estimated = plan.estimated_bytes;
  rep.tree_bytes_limit = plan.limit_bytes;
  rep.point_count = points.size();
  if (!plan.feasible) {
    rep.failure = {Fail::InfeasibleBatchLimit,
                   "even a one-point batch exceeds the memory ceiling",
                   "job '" + job.id + "'", request_id};
    return;
  }
  MP_TRACE(trace, "core", "batch.planned",
           "points=" + std::to_string(points.size()) +
               " batches=[" + join(plan.sizes) + "] cap=" +
               std::to_string(plan.max_batch_size) + " est_bytes=" +
               std::to_string(plan.estimated_bytes) + "/" +
               std::to_string(plan.limit_bytes));

  rep.results.resize(points.size());
  size_t offset = 0;
  size_t batch_no = 0;
  for (size_t take : plan.sizes) {
    ++batch_no;
    std::vector<typename Ops::D> roots(points.begin() + offset,
                                       points.begin() + offset + take);
    MP_TRACE(trace, "core", "product_tree.build",
             "batch=" + std::to_string(batch_no) + " nodes=" +
                 std::to_string(take));
    auto values = evaluate_batch(ops, roots, coeff_hi);
    if (values.size() != take) {
      rep.failure = {Fail::InternalError,
                     "batch produced " + std::to_string(values.size()) +
                         " values for " + std::to_string(take) + " points",
                     "job '" + job.id + "'", request_id};
      return;
    }
    for (size_t i = 0; i < take; ++i) {
      size_t global = offset + i;
      rep.results[global] = {global, job.point_tokens[global],
                             ops.str(values[i])};
    }
    MP_TRACE(trace, "core", "remainder_tree.done",
             "batch=" + std::to_string(batch_no) + " values=" +
                 std::to_string(take));
    offset += take;
  }

  // Defensive, method-independent cross-check with pointwise Horner. The
  // reference expression is recomputed from authored literals; any mismatch is
  // reported as an uncertainty, never silently patched.
  for (size_t i = 0; i < rep.results.size(); ++i) {
    std::string expect;
    if constexpr (Ops::kModular)
      expect = reference::horner_field(job.coeff_tokens, job.point_tokens[i],
                                       ops.modulus());
    else
      expect = reference::horner_integer(job.coeff_tokens, job.point_tokens[i]);
    if (expect != rep.results[i].value) {
      rep.uncertainties.push_back(
          {"TREE_HORNER_CROSSCHECK_MISMATCH",
           "idx=" + std::to_string(i) + " x=" + job.point_tokens[i] +
               " tree=" + rep.results[i].value + " horner=" + expect,
           "job '" + job.id + "'"});
    }
  }

  // Order identity audit: results must sit at their requested indices.
  for (size_t i = 0; i < rep.results.size(); ++i)
    rep.order_preserved = rep.order_preserved && (rep.results[i].index == i);
}

} // namespace

explain::JobReport evaluate_job(const Job& job, const Config& cfg,
                                const std::string& request_id,
                                explain::Trace& trace) {
  explain::JobReport rep;
  rep.request_id = request_id;
  rep.job_id = job.id;
  rep.domain = contract::domain_name(job.domain);
  if (job.modulus) rep.modulus = std::to_string(*job.modulus);
  trace.set_identity(request_id, job.id);

  if (job.domain == Domain::Field && job.modulus) {
    MP_TRACE(trace, "core", "domain.select", "FIELD mod=" +
                                                 std::to_string(*job.modulus) +
                                                 " digit=" + FieldOps::digit_name());
    FieldOps ops(*job.modulus);
    run_domain(ops, job, cfg, request_id, rep, trace);
  } else if (job.domain == Domain::Integer) {
    MP_TRACE(trace, "core", "domain.select",
             std::string("INTEGER digit=") + IntegerOps::digit_name());
    IntegerOps ops;
    run_domain(ops, job, cfg, request_id, rep, trace);
  } else {
    rep.failure = {Fail::DomainMismatch,
                   "kernel entered without a resolved single domain",
                   "job '" + job.id + "'", request_id};
  }
  return rep;
}

namespace reference {
using boost::multiprecision::cpp_int;

// Standalone, independent pointwise Horner over exact integers. Deliberately
// uses no core polynomial type or tree routine.
std::string horner_integer(const std::vector<std::string>& coeff_hi,
                           const std::string& point) {
  cpp_int x;
  try { x = cpp_int(point); } catch (...) { return ""; }
  cpp_int y = 0;
  bool started = false;
  for (const auto& tok : coeff_hi) {
    cpp_int a;
    try { a = cpp_int(tok); } catch (...) { return ""; }
    y = started ? y * x + a : a;
    started = true;
  }
  return y.str();
}

// Independent modular Horner. Reduction is performed on the cpp_int literal
// itself so authored negatives and magnitudes >= p are handled identically to
// the kernel but without calling any field code.
std::string horner_field(const std::vector<std::string>& coeff_hi,
                         const std::string& point, uint64_t mod) {
  using u128 = __uint128_t;
  auto reduce = [&](const std::string& tok) -> uint64_t {
    cpp_int v;
    try { v = cpp_int(tok); } catch (...) { return UINT64_MAX; }
    cpp_int r = v % cpp_int(mod);
    if (r < 0) r += mod;
    return r.convert_to<uint64_t>();
  };
  uint64_t x = reduce(point);
  if (x == UINT64_MAX) return "";
  uint64_t y = 0;
  bool started = false;
  for (const auto& tok : coeff_hi) {
    uint64_t a = reduce(tok);
    if (a == UINT64_MAX) return "";
    uint64_t prod = static_cast<uint64_t>((static_cast<u128>(y) * x) % mod);
    y = started ? prod + a : a;
    if (y >= mod) y -= mod;
    started = true;
  }
  return std::to_string(y);
}
} // namespace reference
} // namespace mp::core
