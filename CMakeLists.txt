cmake_minimum_required(VERSION 3.24)
project(multipoint_eval VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

add_compile_options(-Wall -Wextra)

find_program(GIT_EXECUTABLE git)
set(MP_GIT_DESCRIBE "unknown")
if(GIT_EXECUTABLE)
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" describe --tags --always --dirty
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    OUTPUT_VARIABLE MP_GIT_DESCRIBE OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET)
endif()
configure_file("${CMAKE_SOURCE_DIR}/version.h.in"
               "${CMAKE_BINARY_DIR}/generated/mp/version.h" @ONLY)

include(cmake/ThirdParty.cmake)

add_library(mp_version INTERFACE)
target_include_directories(mp_version INTERFACE "${CMAKE_BINARY_DIR}/generated")

# --- numcontract: numeric contract (domains, parsing, validation, config) ---
add_library(mp_numcontract STATIC
  src/numcontract/src/failure.cpp
  src/numcontract/src/primality.cpp
  src/numcontract/src/config.cpp
  src/numcontract/src/parse.cpp)
target_include_directories(mp_numcontract PUBLIC src/numcontract/include)
target_link_libraries(mp_numcontract PUBLIC mp_boost mp_version)

# --- explain: traces, interpretable reports, uncertainty separation ---
add_library(mp_explain STATIC
  src/explain/src/trace.cpp
  src/explain/src/report.cpp)
target_include_directories(mp_explain PUBLIC src/explain/include)
target_link_libraries(mp_explain PUBLIC mp_numcontract mp_version)

# --- core: polynomial arithmetic, product/remainder trees, batching ---
add_library(mp_core STATIC
  src/core/src/poly.cpp
  src/core/src/rings.cpp
  src/core/src/memory.cpp
  src/core/src/product_tree.cpp
  src/core/src/evaluate.cpp)
target_include_directories(mp_core PUBLIC src/core/include)
target_link_libraries(mp_core PUBLIC mp_numcontract mp_explain mp_boost mp_version)

# --- application backend entry point ---
add_executable(mp_eval src/app/src/main.cpp)
target_link_libraries(mp_eval PRIVATE mp_core)

# --- independent benchmark (Eigen-backed complexity fit) ---
add_executable(polybench src/polybench/src/polybench.cpp)
target_link_libraries(polybench PRIVATE mp_core mp_eigen)

# --- tests ---
enable_testing()
add_executable(mp_unit_tests
  tests/harness/main.cpp
  tests/test_contract.cpp
  tests/test_poly.cpp
  tests/test_evaluation.cpp
  tests/test_report.cpp)
target_include_directories(mp_unit_tests PRIVATE tests)
target_link_libraries(mp_unit_tests PRIVATE mp_core)
target_compile_definitions(mp_unit_tests PRIVATE MP_SOURCE_DIR="${CMAKE_SOURCE_DIR}")
add_test(NAME unit_tests COMMAND mp_unit_tests)
add_test(NAME cli_integration
  COMMAND bash "${CMAKE_SOURCE_DIR}/tests/cli_integration.sh"
          "$<TARGET_FILE:mp_eval>" "${CMAKE_SOURCE_DIR}")
