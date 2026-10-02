#include "test_support/test_framework.hpp"

#include "procrustes/contract.hpp"

#include <limits>

namespace {

procrustes::Request validRequest() {
  procrustes::Request req;
  req.request_id = "req-valid";
  req.source = Eigen::MatrixXd(2, 3);
  req.target = Eigen::MatrixXd(2, 3);
  req.weights = Eigen::VectorXd::Ones(3);
  return req;
}

}  // namespace

TEST_CASE("contract/empty-input-diagnosed") {
  procrustes::Request req;
  req.request_id = "req-empty";
  auto errors = procrustes::validateRequest(req);
  CHECK(!errors.empty());
  CHECK(errors.front().code == procrustes::FailureCode::EmptyInput);
  CHECK(errors.front().severity == procrustes::Severity::Error);
}

TEST_CASE("contract/valid-request-has-no-errors") {
  auto req = validRequest();
  CHECK(procrustes::validateRequest(req).empty());
}

TEST_CASE("contract/size-mismatch-detected") {
  auto req = validRequest();
  req.target = Eigen::MatrixXd(2, 2);
  auto errors = procrustes::validateRequest(req);
  bool found = false;
  for (const auto& e : errors) {
    found = found || e.code == procrustes::FailureCode::SizeMismatch;
  }
  CHECK(found);
}

TEST_CASE("contract/dimension-mismatch-detected") {
  auto req = validRequest();
  req.target = Eigen::MatrixXd(3, 3);
  auto errors = procrustes::validateRequest(req);
  bool found = false;
  for (const auto& e : errors) {
    found = found || e.code == procrustes::FailureCode::DimensionMismatch;
  }
  CHECK(found);
}

TEST_CASE("contract/negative-and-zero-weight-detected") {
  auto neg = validRequest();
  neg.weights = Eigen::Vector3d(-0.1, 1.0, 1.0);
  auto e1 = procrustes::validateRequest(neg);
  CHECK(!e1.empty());
  CHECK(e1.front().code == procrustes::FailureCode::NegativeWeight);

  auto zero = validRequest();
  zero.weights = Eigen::Vector3d(0.0, 0.0, 0.0);
  auto e2 = procrustes::validateRequest(zero);
  CHECK(!e2.empty());
  CHECK(e2.front().code == procrustes::FailureCode::TotalWeightZero);
}

TEST_CASE("contract/nonfinite-values-detected") {
  auto req = validRequest();
  req.source(0, 0) = std::numeric_limits<double>::infinity();
  auto e1 = procrustes::validateRequest(req);
  bool found = false;
  for (const auto& e : e1) {
    found = found || e.code == procrustes::FailureCode::NaNOrInf;
  }
  CHECK(found);

  auto req2 = validRequest();
  req2.rank_tol = 0.0;
  auto e2 = procrustes::validateRequest(req2);
  bool found2 = false;
  for (const auto& e : e2) {
    found2 = found2 || e.code == procrustes::FailureCode::NaNOrInf;
  }
  CHECK(found2);
}

TEST_CASE("contract/enum-string-mapping") {
  CHECK_EQ_STR(procrustes::toString(
                   procrustes::TransformMode::OrthogonalAllowReflection),
               "orthogonal-allow-reflection");
  CHECK_EQ_STR(procrustes::toString(procrustes::FailureCode::TotalWeightZero),
               "total-weight-zero");
  CHECK_EQ_STR(procrustes::toString(procrustes::Severity::Warning),
               "warning");
}
