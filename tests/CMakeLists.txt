# Independent unit tests: concrete numeric assertions with independently
# derived reference answers (closed form, angle scan, hand-built matrices).
add_executable(unit_tests
  test_main.cpp
  unit/test_contract.cpp
  unit/test_fit.cpp
  unit/test_explain.cpp)
target_link_libraries(unit_tests PRIVATE
  procrustes::core procrustes::explain project_options)
target_include_directories(unit_tests PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME unit_tests COMMAND unit_tests)

# Integration tests exercise generated sample data end-to-end, including the
# CLI as a subprocess.
add_executable(integration_tests
  test_main.cpp
  integration/test_sample_pipeline.cpp)
target_link_libraries(integration_tests PRIVATE
  procrustes::core procrustes::explain project_options)
target_include_directories(integration_tests PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(integration_tests PRIVATE
  PROCUSTES_DATA_DIR="${CMAKE_SOURCE_DIR}/data"
  PROCUSTES_CLI_FALLBACK="$<TARGET_FILE:procrustes_cli>")
add_test(NAME integration_tests
         COMMAND integration_tests)
set_tests_properties(integration_tests PROPERTIES
  ENVIRONMENT "PROCRUSTES_CLI=$<TARGET_FILE:procrustes_cli>")

# A CLI smoke test on the committed sample data (no search performed).
add_test(NAME cli_smoke_rigid
         COMMAND procrustes_cli
                 --csv ${CMAKE_SOURCE_DIR}/data/rigid2d_exact.csv
                 --mode rigid --request-id ctest-smoke --format json)
set_tests_properties(cli_smoke_rigid PROPERTIES
  PASS_REGULAR_EXPRESSION "\"ok\": true")

add_test(NAME cli_smoke_collinear
         COMMAND procrustes_cli
                 --csv ${CMAKE_SOURCE_DIR}/data/collinear2d_reflect.csv
                 --mode rigid-reflect --request-id ctest-col --format json)
set_tests_properties(cli_smoke_collinear PROPERTIES
  PASS_REGULAR_EXPRESSION "\"rotation_unique\": false")
