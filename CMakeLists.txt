cmake_minimum_required(VERSION 3.20)
project(bluestein_fft LANGUAGES CXX VERSION 1.0.0)

set(FFT_VERSION "1.0.0" CACHE STRING "Backend version string")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# --- Local, header-only Eigen (vendored under cmake/eigen-3.4.0) -----------
set(EIGEN_LOCAL "${CMAKE_CURRENT_SOURCE_DIR}/cmake/eigen-3.4.0")
if(NOT EXISTS "${EIGEN_LOCAL}/Eigen/Core")
  message(FATAL_ERROR
    "Eigen not found at ${EIGEN_LOCAL}. Run scripts/setup_deps.sh first.")
endif()
add_library(eigen_local INTERFACE)
target_include_directories(eigen_local INTERFACE
  "${EIGEN_LOCAL}" "${EIGEN_LOCAL}/unsupported")
target_compile_definitions(eigen_local INTERFACE EIGEN_NO_AUTOMATIC_RESIZING)

add_library(fft_core STATIC
  src/types.cc
  src/kernel.cc
  src/errors.cc
  src/benchmark.cc)
target_include_directories(fft_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_compile_definitions(fft_core PUBLIC FFT_VERSION="${FFT_VERSION}")
target_link_libraries(fft_core PUBLIC eigen_local)
target_compile_options(fft_core PRIVATE -Wall -Wextra -Wpedantic)

# Runnable service entry point.
add_executable(fft_cli src/main.cc)
target_link_libraries(fft_cli PRIVATE fft_core)

# Independent benchmark.
add_executable(fft_bench bench/bench_main.cc)
target_link_libraries(fft_bench PRIVATE fft_core)

# --- Independent tests ------------------------------------------------------
enable_testing()
add_executable(fft_tests
  test/test_main.cc
  test/test_slice.cc
  test/test_contracts.cc
  test/test_error_categories.cc
  test/test_roundtrip.cc
  test/test_prime_length.cc
  test/test_impulse_large_index.cc
  test/test_large_dynamic.cc
  test/test_eigen_crosscheck.cc)
target_include_directories(fft_tests PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/test")
target_link_libraries(fft_tests PRIVATE fft_core)
add_test(NAME fft_tests COMMAND fft_tests)
