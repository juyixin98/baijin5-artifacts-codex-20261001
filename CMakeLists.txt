cmake_minimum_required(VERSION 3.20)
project(randomized_lmf CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(EIGEN_INCLUDE_DIR "${CMAKE_SOURCE_DIR}/.local/eigen-3.4.0"
    CACHE PATH "Path to extracted Eigen headers")
if(NOT EXISTS "${EIGEN_INCLUDE_DIR}/Eigen/Core")
  message(FATAL_ERROR
    "Eigen headers not found at ${EIGEN_INCLUDE_DIR}. Run ./scripts/setup.sh first.")
endif()

add_library(rlmf_core STATIC
  src/rlmf/error.cpp
  src/rlmf/linalg.cpp
  src/rlmf/factorizer.cpp
  src/rlmf/factorizer_session.cpp
  src/rlmf/errors_explained.cpp
  src/rlmf/fixtures.cpp
  src/rlmf/logger.cpp)
target_include_directories(rlmf_core PUBLIC src ${EIGEN_INCLUDE_DIR})
target_compile_options(rlmf_core PRIVATE -Wall -Wextra -Wpedantic)

enable_testing()

add_executable(rlmf_tests tests/test_main.cpp tests/test_numeric.cpp
  tests/test_contract.cpp tests/test_failure.cpp)
target_link_libraries(rlmf_tests PRIVATE rlmf_core)
add_test(NAME rlmf_tests COMMAND rlmf_tests)

add_executable(rlmf_demo tools/demo.cpp)
target_link_libraries(rlmf_demo PRIVATE rlmf_core)

add_executable(rlmf_bench benchmarks/benchmark.cpp)
target_link_libraries(rlmf_bench PRIVATE rlmf_core)
