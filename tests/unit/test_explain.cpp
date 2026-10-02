#include "test_support/test_framework.hpp"

#include "procrustes/explain.hpp"
#include "procrustes/fit.hpp"
#include "procrustes/logger.hpp"

#include <sstream>

namespace {

procrustes::FitResult collinear2dResult() {
  procrustes::Request req;
  req.request_id = "req-explain";
  req.mode = procrustes::TransformMode::OrthogonalAllowReflection;
  Eigen::MatrixXd p(2, 5);
  Eigen::MatrixXd q(2, 5);
  for (int i = 0; i < 5; ++i) {
    const double x = static_cast<double>(i) - 2.0;
    p.col(i) << x, 0.0;
    q.col(i) << 0.0, x;
  }
  req.source = p;
  req.target = q;
  req.weights = Eigen::VectorXd::Ones(5);
  return procrustes::fit(req);
}

}  // namespace

TEST_CASE("explain/text-sections-and-request-identity") {
  auto result = collinear2dResult();
  const std::string text = procrustes::renderText(result);
  CHECK(text.find("request_id=req-explain") != std::string::npos);
  CHECK(text.find("version=1.0.0") != std::string::npos);
  CHECK(text.find("failures: none") != std::string::npos);
  CHECK(text.find("uncertainties:") != std::string::npos);
  CHECK(text.find("singular-non-unique") != std::string::npos);
  CHECK(text.find("steps:") != std::string::npos);
}

TEST_CASE("explain/failure-is-rendered-separately") {
  procrustes::Request req;
  req.request_id = "req-fail";
  auto result = procrustes::fit(req);
  const std::string text = procrustes::renderText(result);
  CHECK(text.find("status: FAILED") != std::string::npos);
  CHECK(text.find("failures:") != std::string::npos);
  CHECK(text.find("empty-input") != std::string::npos);
}

TEST_CASE("explain/json-is-machine-readable-shape") {
  auto result = collinear2dResult();
  const std::string json = procrustes::renderJson(result);
  CHECK(json.find("\"request_id\": \"req-explain\"") != std::string::npos);
  CHECK(json.find("\"rotation_unique\": false") != std::string::npos);
  CHECK(json.find("\"uncertainties\": [") != std::string::npos);
  CHECK(json.find("singular-non-unique") != std::string::npos);
  CHECK(json.find("\"failures\": []") != std::string::npos);
  CHECK(json.find("\"scale\": 1") != std::string::npos);
}

TEST_CASE("explain/logger-carries-identity-version-location") {
  std::ostringstream os;
  procrustes::Logger logger(os);
  logger.setRequestId("req-log");
  logger.log("unit-component", "a thing happened");
  const std::string line = os.str();
  CHECK(line.find("request_id=req-log") != std::string::npos);
  CHECK(line.find("version=1.0.0") != std::string::npos);
  CHECK(line.find("component=unit-component") != std::string::npos);
  CHECK(line.find("at=") != std::string::npos);
  CHECK(line.find("a thing happened") != std::string::npos);
}
