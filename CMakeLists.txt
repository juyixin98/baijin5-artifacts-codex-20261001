cmake_minimum_required(VERSION 3.20)
project(pade_rational LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Eigen: expected to be fetched into third_party by scripts/fetch_deps.sh.
set(PADE_EIGEN_DIR "${CMAKE_SOURCE_DIR}/third_party/eigen-3.4.0"
    CACHE PATH "Path to extracted Eigen headers")
if(NOT EXISTS "${PADE_EIGEN_DIR}/Eigen/Dense")
  message(FATAL_ERROR "Eigen not found at ${PADE_EIGEN_DIR}; run scripts/fetch_deps.sh")
endif()

option(PADE_WARNINGS_AS_ERRORS "Treat compiler warnings as errors" OFF)

add_library(pade_core STATIC
  src/pade/types.cpp
  src/pade/logging.cpp
  src/pade/polynomial.cpp
  src/pade/solver.cpp
  src/pade/config.cpp
  src/pade/seriesio.cpp
  src/service/service.cpp)
target_include_directories(pade_core PUBLIC
  "${CMAKE_SOURCE_DIR}/include"
  "${PADE_EIGEN_DIR}")
target_compile_options(pade_core PRIVATE -Wall -Wextra -Wpedantic)
if(PADE_WARNINGS_AS_ERRORS)
  target_compile_options(pade_core PRIVATE -Werror)
endif()

enable_testing()
add_subdirectory(tests)

add_subdirectory(src/service)

add_executable(pade_benchmark benchmarks/pade_benchmark.cpp)
target_link_libraries(pade_benchmark PRIVATE pade_core)
