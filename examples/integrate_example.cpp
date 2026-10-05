// Example: build a Gauss-Legendre rule, integrate a smooth function on a
// finite interval, inspect the exactness report, and see the error contract
// in action.

#include <cmath>
#include <cstdio>
#include <string>

#include "gauss/exactness.hpp"
#include "gauss/legendre.hpp"
#include "run_log.hpp"

#ifndef GAUSS_LOG_DIR
#define GAUSS_LOG_DIR "logs"
#endif

int main() {
  gauss::support::RunLog log(GAUSS_LOG_DIR, "example");
  const gauss::LegendreSolver solver;

  // 1. Integrate exp(x) on [0, 1] with a 16-point rule.
  auto rule = solver.compute(16).value().map_to(0.0, 1.0).value();
  double integral = 0.0;
  for (std::size_t i = 0; i < rule.size(); ++i) {
    integral += rule.weights()[i] * std::exp(rule.nodes()[i]);
  }
  const double exact = std::exp(1.0) - 1.0;
  const double error = std::abs(integral - exact);
  std::printf("integral of exp(x) on [0,1], n=16: %.17g\n", integral);
  std::printf("exact e - 1                    : %.17g\n", exact);
  std::printf("absolute error                 : %.3e\n", error);
  log.event({{"event", std::string("integration")},
             {"function", std::string("exp(x)")},
             {"interval", std::string("[0,1]")},
             {"order", 16LL},
             {"computed", integral},
             {"exact", exact},
             {"abs_error", error}});

  // 2. Exactness / residual report for an 8-point rule on [-1, 1].
  auto report = gauss::analyze_exactness(solver.compute(8).value(), 5e-11).value();
  std::printf("\n%s\n", report.summary().c_str());

  // 3. The error contract: distinct, inspectable failure categories.
  std::printf("\nerror contract demo:\n");
  const auto bad_order = solver.compute(0);
  std::printf("  compute(0)                 -> %s\n",
              bad_order.has_value() ? "ok"
                                    : bad_order.error().to_string().c_str());
  const auto bad_interval = rule.map_to(2.0, 1.0);
  std::printf("  map_to(2, 1)               -> %s\n",
              bad_interval.has_value()
                  ? "ok"
                  : bad_interval.error().to_string().c_str());
  gauss::SolverOptions starved;
  starved.max_iterations = 1;
  const auto unconverged = gauss::LegendreSolver(starved).compute(50);
  std::printf("  compute(50), 1 Newton step -> %s\n",
              unconverged.has_value()
                  ? "ok"
                  : unconverged.error().to_string().c_str());
  log.event(
      {{"event", std::string("error_contract_demo")},
       {"compute_0_category",
        std::string(bad_order.has_value()
                        ? "ok"
                        : gauss::to_string(bad_order.error().category))},
       {"map_to_2_1_category",
        std::string(bad_interval.has_value()
                        ? "ok"
                        : gauss::to_string(bad_interval.error().category))},
       {"starved_compute_category",
        std::string(unconverged.has_value()
                        ? "ok"
                        : gauss::to_string(unconverged.error().category))}});
  std::printf("run_id=%s\nlog_file=%s\n", log.run_id().c_str(),
              log.path().c_str());
  return 0;
}
