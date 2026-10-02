add_executable(test_pade
  test_framework.cpp
  test_exact_exp.cpp
  test_rank_degeneracy.cpp
  test_evaluation.cpp
  test_common_factor.cpp
  test_oracles.cpp
  test_config_layer.cpp)
target_link_libraries(test_pade PRIVATE pade_core)
add_test(NAME pade_unit_tests COMMAND test_pade)
