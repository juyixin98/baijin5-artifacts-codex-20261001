cmake_minimum_required(VERSION 3.16)
project(cheb_clenshaw LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# --- Project-local, pinned dependencies (no system Eigen/Boost assumed) -----
set(CHE_DEPS_DIR "${CMAKE_SOURCE_DIR}/.deps" CACHE PATH
    "Directory holding project-local pinned dependencies")
set(CHE_EIGEN_DIR "${CHE_DEPS_DIR}/eigen-3.4.0" CACHE PATH "Pinned Eigen tree")
set(CHE_BOOST_DIR "${CHE_DEPS_DIR}/boost_1_85_0" CACHE PATH "Pinned Boost tree")

if(NOT EXISTS "${CHE_EIGEN_DIR}/Eigen/Core")
  message(FATAL_ERROR
    "Pinned Eigen not found at ${CHE_EIGEN_DIR}. Run scripts/fetch_deps.sh first.")
endif()
if(NOT EXISTS "${CHE_BOOST_DIR}/boost/multiprecision/cpp_dec_float.hpp")
  message(FATAL_ERROR
    "Pinned Boost.Multiprecision not found at ${CHE_BOOST_DIR}. "
    "Run scripts/fetch_deps.sh first.")
endif()

add_library(cheb_deps INTERFACE)
target_include_directories(cheb_deps INTERFACE
  "${CMAKE_SOURCE_DIR}/include"
  "${CHE_EIGEN_DIR}"
  "${CHE_BOOST_DIR}")

# An Eigen-using translation unit proves the declared stack is real: build a
# small DCT check with Eigen vectors in the numeric contract TU consumers.
find_package(Threads REQUIRED)

add_library(chebcore STATIC
  src/numeric_contract.cpp
  src/kernel.cpp
  src/error_estimate.cpp
  src/diagnostics.cpp
  src/reference.cpp
  src/fixtures.cpp)
target_link_libraries(chebcore PUBLIC cheb_deps Threads::Threads)
target_compile_options(chebcore PRIVATE -Wall -Wextra -Wpedantic)

enable_testing()
include(CTest)

set(CHE_TESTS
  test_contract
  test_kernel
  test_error
  test_reference
  test_fixtures
  test_diagnostics)

foreach(t IN LISTS CHE_TESTS)
  add_executable(${t} tests/${t}.cpp)
  target_include_directories(${t} PRIVATE tests)
  target_link_libraries(${t} PRIVATE chebcore)
  target_compile_options(${t} PRIVATE -Wall -Wextra -Wpedantic)
  add_test(NAME ${t} COMMAND ${t})
endforeach()

add_executable(cheb_diag tools/cheb_diag.cpp)
target_link_libraries(cheb_diag PRIVATE chebcore)
target_compile_options(cheb_diag PRIVATE -Wall -Wextra -Wpedantic)

add_executable(gen_fixtures tools/gen_fixtures.cpp)
target_link_libraries(gen_fixtures PRIVATE chebcore)
target_compile_options(gen_fixtures PRIVATE -Wall -Wextra -Wpedantic)
