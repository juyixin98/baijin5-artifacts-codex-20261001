cmake_minimum_required(VERSION 3.20)
project(pade_approx LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# Eigen: project-local header-only checkout (see scripts/setup_deps.sh).
# Prefer local third_party; allow -DEIGEN_INCLUDE_DIR override.
if(NOT DEFINED EIGEN_INCLUDE_DIR)
  file(GLOB PADE_EIGEN_CAND
       "${CMAKE_SOURCE_DIR}/third_party/eigen-3.4.0/Eigen/Dense")
  if(PADE_EIGEN_CAND)
    set(EIGEN_INCLUDE_DIR "${CMAKE_SOURCE_DIR}/third_party/eigen-3.4.0")
  else()
    find_path(EIGEN_INCLUDE_DIR Eigen/Dense)
  endif()
endif()
if(NOT EXISTS "${EIGEN_INCLUDE_DIR}/Eigen/Dense")
  message(FATAL_ERROR
    "Eigen headers not found. Run scripts/setup_deps.sh first "
    "(fetches Eigen 3.4.0 into third_party/), or pass -DEIGEN_INCLUDE_DIR=...")
endif()
message(STATUS "Using Eigen: ${EIGEN_INCLUDE_DIR}")

add_library(pade_core STATIC
  src/types.cpp
  src/polynomial.cpp
  src/solver.cpp
  src/io.cpp)
target_include_directories(pade_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
# Eigen 3.4.0 triggers a known spurious -Wmaybe-uninitialized on GCC 13/14;
# include it as a system header so its internal diagnostics stay out of our build.
target_include_directories(pade_core SYSTEM PUBLIC ${EIGEN_INCLUDE_DIR})
target_compile_options(pade_core PRIVATE -Wall -Wextra -Wpedantic)

add_executable(pade_cli src/main.cpp)
target_link_libraries(pade_cli PRIVATE pade_core)
target_compile_definitions(pade_cli PRIVATE PADE_VERSION="${PROJECT_VERSION}")

add_executable(gen_fixtures app/gen_fixtures.cpp)
target_link_libraries(gen_fixtures PRIVATE pade_core)

enable_testing()
add_executable(pade_tests
  tests/test_main.cpp
  tests/test_pade.cpp
  tests/test_degeneracy.cpp
  tests/test_contract.cpp)
target_include_directories(pade_tests PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/tests)
target_link_libraries(pade_tests PRIVATE pade_core)
target_compile_definitions(pade_tests PRIVATE PADE_VERSION="${PROJECT_VERSION}")
add_test(NAME pade_tests COMMAND pade_tests)

add_executable(pade_bench bench/bench_pade.cpp)
target_link_libraries(pade_bench PRIVATE pade_core)
