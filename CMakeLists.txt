cmake_minimum_required(VERSION 3.20)
project(gauss_quadrature VERSION 1.0.0 LANGUAGES CXX)

# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "build type" FORCE)
endif()

add_library(gauss_warnings INTERFACE)
target_compile_options(gauss_warnings INTERFACE -Wall -Wextra -Wpedantic)

# ---------------------------------------------------------------------------
# Eigen 3.4.0 (project-local, fetched by scripts/bootstrap.sh; see
# third_party/LOCK.md for the pinned version and hash)
# ---------------------------------------------------------------------------
set(GAUSS_EIGEN_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/eigen-3.4.0")
if(NOT EXISTS "${GAUSS_EIGEN_DIR}/Eigen/Dense")
  message(FATAL_ERROR
    "Eigen not found at ${GAUSS_EIGEN_DIR}. Run scripts/bootstrap.sh first.")
endif()
add_library(gauss_eigen INTERFACE)
target_include_directories(gauss_eigen SYSTEM INTERFACE "${GAUSS_EIGEN_DIR}")
add_library(Eigen3::Eigen ALIAS gauss_eigen)

# ---------------------------------------------------------------------------
# Core library: error contract, rule data contract, Newton kernel,
# exactness/residual analysis
# ---------------------------------------------------------------------------
add_library(gauss_core STATIC
  src/error.cpp
  src/rule.cpp
  src/legendre.cpp
  src/exactness.cpp
)
target_include_directories(gauss_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(gauss_core PUBLIC Eigen3::Eigen PRIVATE gauss_warnings)

# ---------------------------------------------------------------------------
# Independent benchmark module: Golub-Welsch reference (Eigen eigensolver)
# ---------------------------------------------------------------------------
add_library(gauss_benchmark STATIC benchmark/golub_welsch.cpp)
target_include_directories(gauss_benchmark PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(gauss_benchmark PUBLIC gauss_core PRIVATE gauss_warnings)

# Structured run-log support (header-only) shared by tests/benchmark/example.
add_library(gauss_support INTERFACE)
target_include_directories(gauss_support INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/support")

set(GAUSS_LOG_DIR "${CMAKE_BINARY_DIR}/logs")
file(MAKE_DIRECTORY "${GAUSS_LOG_DIR}")

# ---------------------------------------------------------------------------
# Reference fixture data: generated at build time by a C++ tool from
# hand-derived closed forms (never by the kernel under test)
# ---------------------------------------------------------------------------
add_executable(gen_reference_fixtures tools/gen_reference_fixtures.cpp)
target_link_libraries(gen_reference_fixtures PRIVATE gauss_warnings)

set(GAUSS_FIXTURE_DIR "${CMAKE_BINARY_DIR}/fixtures")
set(GAUSS_FIXTURE_FILE "${GAUSS_FIXTURE_DIR}/gauss_legendre_reference.tsv")
file(MAKE_DIRECTORY "${GAUSS_FIXTURE_DIR}")
add_custom_command(
  OUTPUT "${GAUSS_FIXTURE_FILE}"
  COMMAND gen_reference_fixtures "${GAUSS_FIXTURE_FILE}"
  DEPENDS gen_reference_fixtures
  COMMENT "generating hand-derived reference fixtures"
)
add_custom_target(reference_fixtures ALL DEPENDS "${GAUSS_FIXTURE_FILE}")

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
add_executable(gauss_tests
  tests/test_main.cpp
  tests/test_rule_behavior.cpp
  tests/test_reference.cpp
  tests/test_exactness.cpp
  tests/test_errors.cpp
)
target_link_libraries(gauss_tests
  PRIVATE gauss_core gauss_benchmark gauss_support gauss_warnings)
target_compile_definitions(gauss_tests PRIVATE
  GAUSS_FIXTURE_PATH="${GAUSS_FIXTURE_FILE}"
  GAUSS_LOG_DIR="${GAUSS_LOG_DIR}"
)
add_dependencies(gauss_tests reference_fixtures)

# ---------------------------------------------------------------------------
# Benchmark runner and example
# ---------------------------------------------------------------------------
add_executable(gauss_benchmark_runner benchmark/benchmark_main.cpp)
target_link_libraries(gauss_benchmark_runner
  PRIVATE gauss_benchmark gauss_support gauss_warnings)
target_compile_definitions(gauss_benchmark_runner PRIVATE
  GAUSS_LOG_DIR="${GAUSS_LOG_DIR}")

add_executable(gauss_example examples/integrate_example.cpp)
target_link_libraries(gauss_example
  PRIVATE gauss_core gauss_support gauss_warnings)
target_compile_definitions(gauss_example PRIVATE
  GAUSS_LOG_DIR="${GAUSS_LOG_DIR}")

# ---------------------------------------------------------------------------
# CTest wiring
# ---------------------------------------------------------------------------
enable_testing()
add_test(NAME unit_and_contract COMMAND gauss_tests)
add_test(NAME independent_benchmark COMMAND gauss_benchmark_runner)
add_test(NAME example_invocation COMMAND gauss_example)
