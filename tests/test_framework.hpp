#pragma once

// Minimal self-contained test framework (no external test dependency).
//
// Every check is logged as a structured JSONL event with the expression,
// the measured/expected values, the tolerance and a human rationale, so a
// failure can be replayed from the log file alone.

#include <cmath>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "gauss/error.hpp"
#include "run_log.hpp"

namespace gauss::test {

class TestContext {
 public:
  TestContext(support::RunLog& log, std::string test_name)
      : log_(log), test_name_(std::move(test_name)) {}

  const std::string& test_name() const noexcept { return test_name_; }
  int checks() const noexcept { return checks_; }
  int failures() const noexcept { return failures_; }

  // Records a boolean check.
  void check(bool ok, std::string_view expression, std::string_view rationale,
             std::string_view file, int line,
             std::vector<support::Field> extra = {}) {
    record(ok, expression, rationale, file, line, std::move(extra));
  }

  // Records a floating-point closeness check |actual - expected| <= tol.
  void check_close(double actual, double expected, double tol,
                   std::string_view expression, std::string_view rationale,
                   std::string_view file, int line,
                   std::vector<support::Field> extra = {}) {
    const double diff = std::abs(actual - expected);
    std::vector<support::Field> fields;
    fields.emplace_back("actual", actual);
    fields.emplace_back("expected", expected);
    fields.emplace_back("abs_diff", diff);
    fields.emplace_back("tolerance", tol);
    fields.insert(fields.end(), extra.begin(), extra.end());
    record(diff <= tol, expression, rationale, file, line, std::move(fields));
  }

  // Records that `result` must be an error of exactly `expected_category`.
  template <typename T>
  void check_error(const Result<T>& result, ErrorCategory expected_category,
                   std::string_view expression, std::string_view rationale,
                   std::string_view file, int line) {
    const bool ok =
        !result.has_value() && result.error().category == expected_category;
    std::vector<support::Field> fields;
    fields.emplace_back("expected_category",
                        std::string(to_string(expected_category)));
    if (result.has_value()) {
      fields.emplace_back("actual_outcome", std::string("unexpected_success"));
    } else {
      fields.emplace_back("actual_category",
                          std::string(to_string(result.error().category)));
      fields.emplace_back("error_message", result.error().message);
      std::ostringstream diag;
      for (const auto& [k, v] : result.error().diagnostics) {
        diag << k << '=' << v << ';';
      }
      fields.emplace_back("error_diagnostics", diag.str());
    }
    record(ok, expression, rationale, file, line, std::move(fields));
  }

  // Logs intermediate state that is not itself a judgment.
  void state(std::vector<support::Field> fields) {
    std::vector<support::Field> all{{"event", std::string("state")},
                                    {"test", test_name_}};
    all.insert(all.end(), fields.begin(), fields.end());
    log_.event(all);
  }

 private:
  void record(bool ok, std::string_view expression, std::string_view rationale,
              std::string_view file, int line,
              std::vector<support::Field> fields) {
    ++checks_;
    if (!ok) ++failures_;
    std::vector<support::Field> all{{"event", std::string("check")},
                                    {"test", test_name_},
                                    {"expression", std::string(expression)},
                                    {"status", std::string(ok ? "pass" : "fail")},
                                    {"rationale", std::string(rationale)},
                                    {"file", std::string(file)},
                                    {"line", static_cast<long long>(line)}};
    all.insert(all.end(), fields.begin(), fields.end());
    log_.event(all);
    if (!ok) {
      std::cerr << "FAIL [" << test_name_ << "] " << file << ':' << line << "  "
                << expression << "  -- " << rationale << '\n';
    }
  }

  support::RunLog& log_;
  std::string test_name_;
  int checks_ = 0;
  int failures_ = 0;
};

struct TestCase {
  std::string name;
  void (*run)(TestContext&);
};

int run_all(support::RunLog& log, const std::vector<TestCase>& tests);

}  // namespace gauss::test

#define GAUSS_CHECK(ctx, condition, rationale) \
  (ctx).check(static_cast<bool>(condition), #condition, rationale, __FILE__, __LINE__)

#define GAUSS_CHECK_CLOSE(ctx, actual, expected, tol, rationale) \
  (ctx).check_close((actual), (expected), (tol), #actual, rationale, __FILE__, __LINE__)

#define GAUSS_CHECK_ERROR(ctx, result, category, rationale) \
  (ctx).check_error((result), (category), #result, rationale, __FILE__, __LINE__)
