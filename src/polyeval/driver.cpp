#include "polyeval/driver.hpp"

#include <algorithm>
#include <sstream>

#include "polyeval/json.hpp"
#include "polyeval/kernel.hpp"

namespace polyeval::driver {

using contract::Big;
using contract::FailCode;

Big horner_exact(const std::vector<Big>& coeff, const Big& x) {
  Big r(0);
  for (auto it = coeff.rbegin(); it != coeff.rend(); ++it) r = r * x + *it;
  return r;
}

namespace {

std::size_t resolve_batch(const contract::ParsedRequest& req,
                          const config::EvalConfig& cfg, bool field_mode,
                          std::uint64_t& chosen_slots) {
  const std::size_t n = req.points.size();
  if (req.batch_size) {
    std::size_t b = std::min<std::size_t>(n, static_cast<std::size_t>(*req.batch_size));
    chosen_slots = kernel::product_tree_slots(b);
    return b;
  }
  const std::uint64_t configured_fixed =
      field_mode ? cfg.field_batch_size : cfg.exact_batch_size;
  if (configured_fixed) {
    std::size_t b = std::min<std::size_t>(n, static_cast<std::size_t>(configured_fixed));
    chosen_slots = kernel::product_tree_slots(b);
    return b;
  }
  const std::uint64_t budget = req.memory_bytes.value_or(cfg.memory_bytes);
  const std::uint64_t bps = field_mode ? cfg.field_bytes_per_scalar
                                       : cfg.exact_bytes_per_scalar;
  std::size_t b = kernel::choose_batch_size(n, budget, bps);
  chosen_slots = b == 0 ? kernel::product_tree_slots(1) : kernel::product_tree_slots(b);
  return b;
}

contract::Response run_exact(const contract::ParsedRequest& req,
                             const config::EvalConfig& cfg, log::JsonlLogger& lg) {
  using kernel::Poly;
  contract::Response res;
  res.id = req.id;
  res.mode = contract::Mode::Exact;
  res.point_count = req.points.size();

  std::uint64_t slots = 0;
  std::size_t batch = resolve_batch(req, cfg, false, slots);
  if (batch == 0) {
    res.status = FailCode::TooSmallMemoryBudget;
    res.failure_reason = "memory budget too small even for a single-point product tree";
    lg.emit(req.id, "batch", "driver.cpp:resolve_batch", "batch=0 budget exhausted", "failure");
    return res;
  }
  res.batch_size_used = batch;
  res.tree_slots = slots;
  lg.emit(req.id, "batch", "driver.cpp:resolve_batch",
          "mode=EXACT batch=" + std::to_string(batch) + " slots=" + std::to_string(slots));

  Poly<Big> p;
  p.coeff = req.coeff;
  std::vector<Big> all_values(req.points.size());

  kernel::stats().reset();
  for (std::size_t start = 0; start < req.points.size(); start += batch) {
    std::size_t end = std::min(start + batch, req.points.size());
    std::vector<Big> chunk(req.points.begin() + start, req.points.begin() + end);
    lg.emit(req.id, "build", "driver.cpp:run_exact",
            "product tree for [" + std::to_string(start) + "," + std::to_string(end) + ")");
    std::vector<Big> vals = kernel::eval_batch(p, chunk);
    for (std::size_t i = 0; i < chunk.size(); ++i) all_values[start + i] = vals[i];
  }
  res.multiply_scalar_ops = kernel::stats().multiply_scalar_ops;
  res.remainder_scalar_ops = kernel::stats().remainder_scalar_ops;

  const std::uint64_t bit_cap = req.crosscheck_bits.value_or(cfg.crosscheck_bits);
  for (std::size_t i = 0; i < req.points.size(); ++i) {
    contract::PointResult pr;
    pr.index = i;
    pr.point = req.points[i];
    pr.value = all_values[i];
    if (explainer::bit_length(all_values[i]) <= bit_cap) {
      Big want = horner_exact(req.coeff, req.points[i]);
      lg.emit(req.id, "crosscheck", "driver.cpp:run_exact",
              "point[" + std::to_string(i) + "] Horner compare");
      if (want == all_values[i]) {
        pr.exact_crosschecked = true;
      } else {
        pr.uncertain = true;
        pr.note = "tree/Horner mismatch (internal error)";
        lg.emit(req.id, "crosscheck", "driver.cpp:run_exact",
                "point[" + std::to_string(i) + "] MISMATCH", "failure");
      }
    } else {
      pr.uncertain = true;
      pr.note = "bit length > crosscheck_bits=" + std::to_string(bit_cap);
      lg.emit(req.id, "crosscheck", "driver.cpp:run_exact",
              "point[" + std::to_string(i) + "] skipped (bit cap)", "uncertain");
    }
    res.results.push_back(std::move(pr));
  }
  return res;
}

contract::Response run_field(const contract::ParsedRequest& req,
                             const config::EvalConfig& cfg, log::JsonlLogger& lg) {
  using kernel::Poly;
  using field::ModInt;
  contract::Response res;
  res.id = req.id;
  res.mode = contract::Mode::Field;
  res.prime = req.prime;
  res.point_count = req.points.size();
  const std::uint64_t prime = *req.prime;

  std::uint64_t slots = 0;
  std::size_t batch = resolve_batch(req, cfg, true, slots);
  if (batch == 0) {
    res.status = FailCode::TooSmallMemoryBudget;
    res.failure_reason = "memory budget too small even for a single-point product tree";
    lg.emit(req.id, "batch", "driver.cpp:resolve_batch", "batch=0 budget exhausted", "failure");
    return res;
  }
  res.batch_size_used = batch;
  res.tree_slots = slots;

  field::ModIntScope scope(prime);
  Poly<ModInt> p;
  p.coeff.reserve(req.coeff.size());
  for (const Big& c : req.coeff) p.coeff.push_back(ModInt::raw(c.convert_to<field::u64>()));
  std::vector<ModInt> fpts;
  fpts.reserve(req.points.size());
  for (const Big& x : req.points) fpts.push_back(ModInt::raw(x.convert_to<field::u64>()));

  std::vector<ModInt> all_values(req.points.size());
  kernel::stats().reset();
  for (std::size_t start = 0; start < fpts.size(); start += batch) {
    std::size_t end = std::min(start + batch, fpts.size());
    std::vector<ModInt> chunk(fpts.begin() + start, fpts.begin() + end);
    lg.emit(req.id, "build", "driver.cpp:run_field",
            "product tree for [" + std::to_string(start) + "," + std::to_string(end) + ")");
    std::vector<ModInt> vals = kernel::eval_batch(p, chunk);
    for (std::size_t i = 0; i < chunk.size(); ++i) all_values[start + i] = vals[i];
  }
  res.multiply_scalar_ops = kernel::stats().multiply_scalar_ops;
  res.remainder_scalar_ops = kernel::stats().remainder_scalar_ops;

  for (std::size_t i = 0; i < req.points.size(); ++i) {
    contract::PointResult pr;
    pr.index = i;
    pr.point = req.points[i];
    pr.value = Big(all_values[i].v);
    pr.note = "residue mod " + std::to_string(prime);
    res.results.push_back(std::move(pr));
  }
  lg.emit(req.id, "descend", "driver.cpp:run_field",
          "field remainder tree complete p=" + std::to_string(prime));
  return res;
}

}  // namespace

RunReport run(const contract::ParseOutcome& parsed, const config::EvalConfig& cfg,
              log::JsonlLogger& logger) {
  RunReport report;
  report.parse_failures = parsed.failures;
  for (const auto& f : parsed.failures) logger.emit(f.id, "parse", "contract.cpp", f.message, "failure");
  for (const contract::ParsedRequest& req : parsed.requests) {
    contract::Response res;
    res.request_index = static_cast<std::size_t>(&req - &parsed.requests[0]);
    try {
      logger.emit(req.id, "request", "driver.cpp:run",
                  std::string("mode=") + contract::mode_name(req.mode) +
                      " points=" + std::to_string(req.points.size()) +
                      " degree=" + std::to_string(req.coeff.size() - 1));
      res = (req.mode == contract::Mode::Field) ? run_field(req, cfg, logger)
                                                : run_exact(req, cfg, logger);
    } catch (const std::exception& e) {
      res.id = req.id;
      res.mode = req.mode;
      res.prime = req.prime;
      res.point_count = req.points.size();
      res.status = FailCode::InternalError;
      res.failure_reason = e.what();
      logger.emit(req.id, "request", "driver.cpp:run", e.what(), "failure");
    }
    logger.emit(req.id, "response", "driver.cpp:run",
                fail_code_name(res.status) + std::string(" results=") +
                    std::to_string(res.results.size()),
                res.status == FailCode::Ok ? "ok" : "failure");
    report.responses.push_back(std::move(res));
  }
  return report;
}

std::string response_to_json(const contract::Response& r,
                             const std::vector<explainer::PointExplanation>& explanations) {
  std::ostringstream os;
  os << "{";
  os << json::field("id", json::quote(r.id));
  os << json::field("mode", json::quote(contract::mode_name(r.mode)));
  os << json::field("status", json::quote(contract::fail_code_name(r.status)));
  if (r.prime) os << json::field("prime", std::to_string(*r.prime));
  if (r.status != FailCode::Ok) os << json::field("failure_reason", json::quote(r.failure_reason));
  os << json::field("point_count", std::to_string(r.point_count));
  os << json::field("batch_size_used", std::to_string(r.batch_size_used));
  os << json::field("tree_slots", std::to_string(r.tree_slots));
  os << json::field("multiply_scalar_ops", std::to_string(r.multiply_scalar_ops));
  os << json::field("remainder_scalar_ops", std::to_string(r.remainder_scalar_ops));
  os << "\"points\":[";
  for (std::size_t i = 0; i < r.results.size(); ++i) {
    const auto& pr = r.results[i];
    const explainer::PointExplanation* ex =
        i < explanations.size() ? &explanations[i] : nullptr;
    os << "{";
    os << json::field("index", std::to_string(pr.index));
    os << json::field("x", json::quote(pr.point.str()));
    os << json::field("y", json::quote(pr.value.str()));
    os << json::field("uncertain", pr.uncertain ? "true" : "false");
    os << json::field("exact_crosschecked", pr.exact_crosschecked ? "true" : "false");
    std::string status = ex ? ex->status : std::string("VALUE");
    os << json::field("status", json::quote(status));
    std::string interp = ex ? ex->interpretation : pr.note;
    os << json::field("interpretation", json::quote(interp), false);
    os << "}" << (i + 1 < r.results.size() ? "," : "");
  }
  os << "]}";
  return os.str();
}

std::string failure_to_json(const contract::ParseFailure& f) {
  std::ostringstream os;
  os << "{";
  os << json::field("id", json::quote(f.id));
  os << json::field("request_index", std::to_string(f.request_index));
  os << json::field("status", json::quote(contract::fail_code_name(f.code)));
  os << json::field("failure_reason", json::quote(f.message), false);
  os << "}";
  return os.str();
}

}  // namespace polyeval::driver
