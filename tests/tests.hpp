#pragma once

// Registry of all independent test cases. Each function is defined in its
// own translation unit and registered in test_main.cpp.

#include "test_framework.hpp"

namespace gauss::test {

// test_rule_behavior.cpp
void test_symmetry_weights_positivity_sum(TestContext& ctx);
void test_interval_mapping(TestContext& ctx);

// test_reference.cpp
void test_hand_computed_fixtures(TestContext& ctx);
void test_cross_check_golub_welsch(TestContext& ctx);

// test_exactness.cpp
void test_polynomial_exactness(TestContext& ctx);
void test_exactness_on_mapped_interval(TestContext& ctx);

// test_errors.cpp
void test_invalid_input(TestContext& ctx);
void test_resource_exhaustion(TestContext& ctx);
void test_non_convergence_rejected(TestContext& ctx);
void test_state_conflict(TestContext& ctx);

}  // namespace gauss::test
