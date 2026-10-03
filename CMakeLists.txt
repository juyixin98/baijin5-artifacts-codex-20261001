cmake_minimum_required(VERSION 3.20)
project(cheb_clenshaw LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# Local, pinned Eigen (see scripts/fetch_deps.sh).
set(EIGEN_LOCAL_ROOT "${CMAKE_SOURCE_DIR}/deps/eigen-3.4.0"
    CACHE PATH "Path to locally extracted Eigen 3.4.0")
if(NOT EXISTS "${EIGEN_LOCAL_ROOT}/Eigen/Dense")
  message(FATAL_ERROR
    "Eigen not found at ${EIGEN_LOCAL_ROOT}. Run scripts/fetch_deps.sh first.")
endif()

add_library(eigen_headers INTERFACE)
target_include_directories(eigen_headers INTERFACE "${EIGEN_LOCAL_ROOT}")

add_library(cheb_core INTERFACE)
target_include_directories(cheb_core INTERFACE "${CMAKE_SOURCE_DIR}/src")
target_link_libraries(cheb_core INTERFACE eigen_headers)

add_library(cheb_reference INTERFACE)
target_include_directories(cheb_reference INTERFACE "${CMAKE_SOURCE_DIR}/src")

enable_testing()

set(CHEB_TESTS
  test_mapping
  test_expansion
  test_clenshaw
  test_errors
  test_diagnostics
  test_reference_crosscheck
  test_generated_fixture
  test_e2e)

foreach(t ${CHEB_TESTS})
  add_executable(${t} tests/${t}.cpp)
  target_link_libraries(${t} PRIVATE cheb_core cheb_reference)
  target_compile_options(${t} PRIVATE -Wall -Wextra -Wpedantic)
  add_test(NAME ${t} COMMAND ${t}
           WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endforeach()

add_dependencies(test_generated_fixture gen_fixture)
target_compile_definitions(test_generated_fixture PRIVATE
  GEN_FIXTURE_BIN="$<TARGET_FILE:gen_fixture>")

add_executable(bench_ref tools/bench_ref.cpp)
target_link_libraries(bench_ref PRIVATE cheb_reference)

add_executable(gen_fixture tools/gen_fixture.cpp)
target_link_libraries(gen_fixture PRIVATE cheb_reference)

add_executable(cheb_demo tools/cheb_demo.cpp)
target_link_libraries(cheb_demo PRIVATE cheb_core)
