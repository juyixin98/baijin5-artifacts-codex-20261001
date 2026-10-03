cmake_minimum_required(VERSION 3.20)
project(bluestein_fft CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wpedantic -O2)

# Local Eigen (header-only), fetched by scripts/fetch_deps.sh.
set(EIGEN_LOCAL_DIR "${CMAKE_SOURCE_DIR}/third_party/eigen-src")
if(EXISTS "${EIGEN_LOCAL_DIR}/Eigen/Dense")
  add_library(eigen_headers INTERFACE)
  target_include_directories(eigen_headers INTERFACE "${EIGEN_LOCAL_DIR}")
  message(STATUS "Using local Eigen from ${EIGEN_LOCAL_DIR}")
else()
  include(FetchContent)
  FetchContent_Declare(
    eigen
    URL "https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz")
  FetchContent_MakeAvailable(eigen)
  add_library(eigen_headers ALIAS Eigen3::Eigen)
endif()

# --- multi-module backend libraries ---
add_library(common STATIC src/common/status.cpp)
target_include_directories(common PUBLIC src)

add_library(reference STATIC src/reference/reference_dft.cpp)
target_include_directories(reference PUBLIC src)
target_link_libraries(reference PUBLIC common)

add_library(mathcore STATIC src/mathcore/fft.cpp src/mathcore/chirp.cpp)
target_include_directories(mathcore PUBLIC src)
target_link_libraries(mathcore PUBLIC common eigen_headers)

add_library(contract STATIC src/contract/numeric_contract.cpp)
target_include_directories(contract PUBLIC src)
target_link_libraries(contract PUBLIC common reference mathcore)

add_library(bench STATIC src/bench/benchmark.cpp)
target_include_directories(bench PUBLIC src)
target_link_libraries(bench PUBLIC common mathcore reference contract)

# --- runnable service entrypoint ---
add_library(svc STATIC src/service/response.cpp)
target_include_directories(svc PUBLIC src)
target_link_libraries(svc PUBLIC common)

add_executable(fft_service src/service/main.cpp)
target_link_libraries(fft_service PRIVATE svc bench contract mathcore reference common)

# --- independent tests ---
enable_testing()

function(add_unit_test name)
  add_executable(${name} ${ARGN})
  target_include_directories(${name} PRIVATE tests src)
  target_link_libraries(${name} PRIVATE svc mathcore reference contract bench common)
  add_test(NAME ${name} COMMAND ${name})
endfunction()

add_unit_test(test_slice tests/test_slice.cpp)
add_unit_test(test_small_length_reference tests/test_small_length_reference.cpp)
add_unit_test(test_prime_lengths tests/test_prime_lengths.cpp)
add_unit_test(test_impulse tests/test_impulse.cpp)
add_unit_test(test_large_dynamic tests/test_large_dynamic.cpp)
add_unit_test(test_inverse_normalization tests/test_inverse_normalization.cpp)
add_unit_test(test_convolution_geometry tests/test_convolution_geometry.cpp)
add_unit_test(test_chirp_phase tests/test_chirp_phase.cpp)
add_unit_test(test_error_categories tests/test_error_categories.cpp)
add_unit_test(test_memory_report tests/test_memory_report.cpp)
add_unit_test(test_request_identity tests/test_request_identity.cpp)
add_unit_test(test_eigen_api tests/test_eigen_api.cpp)
target_link_libraries(test_eigen_api PRIVATE eigen_headers)

add_executable(test_cli_pipeline tests/test_cli_pipeline.cpp)
target_include_directories(test_cli_pipeline PRIVATE tests src)
target_compile_definitions(test_cli_pipeline PRIVATE
  TEST_BINARY_DIR="${CMAKE_BINARY_DIR}")
target_link_libraries(test_cli_pipeline PRIVATE common)
add_test(NAME test_cli_pipeline COMMAND test_cli_pipeline)
