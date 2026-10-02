cmake_minimum_required(VERSION 3.20)

project(procrustes_fit
  VERSION 1.0.0
  DESCRIPTION "Weighted orthogonal and similarity transform fit for paired point sets"
  LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# Project-local Eigen (bootstrap via scripts/setup_deps.sh). Allow override via
# -DEIGEN_ROOT=... or EIGEN_ROOT env var; otherwise use .deps.
if(NOT DEFINED EIGEN_ROOT)
  if(DEFINED ENV{EIGEN_ROOT})
    set(EIGEN_ROOT "$ENV{EIGEN_ROOT}")
  else()
    set(EIGEN_ROOT "${CMAKE_SOURCE_DIR}/.deps/eigen-3.4.0")
  endif()
endif()

if(NOT EXISTS "${EIGEN_ROOT}/Eigen/Core")
  message(FATAL_ERROR
    "Eigen headers not found at ${EIGEN_ROOT}. "
    "Run scripts/setup_deps.sh first (bootstraps CMake and Eigen into .deps).")
endif()

message(STATUS "Using Eigen from: ${EIGEN_ROOT}")

add_library(eigen_headers INTERFACE)
target_include_directories(eigen_headers INTERFACE "${EIGEN_ROOT}")

add_library(project_options INTERFACE)
target_compile_features(project_options INTERFACE cxx_std_20)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(project_options INTERFACE -Wall -Wextra -Wpedantic)
endif()

enable_testing()

add_subdirectory(src/procrustes_contract)
add_subdirectory(src/procrustes_core)
add_subdirectory(src/procrustes_explain)
add_subdirectory(src/procrustes_bench)
add_subdirectory(apps)
add_subdirectory(tests)
