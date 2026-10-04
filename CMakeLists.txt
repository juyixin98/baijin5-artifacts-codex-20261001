cmake_minimum_required(VERSION 3.20)
project(gauss_legendre LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Eigen：使用项目内 .deps 下的纯头文件（scripts/fetch_deps.sh 负责获取）。
set(GAUSSLE_EIGEN_ROOT "${CMAKE_SOURCE_DIR}/.deps/eigen-3.4.0"
    CACHE PATH "Eigen 3.4.0 头文件根目录")
if(NOT EXISTS "${GAUSSLE_EIGEN_ROOT}/Eigen/Core")
  message(FATAL_ERROR
    "未找到 Eigen：${GAUSSLE_EIGEN_ROOT}/Eigen/Core 不存在。\n"
    "请先运行 scripts/fetch_deps.sh。")
endif()

add_compile_options(-Wall -Wextra -Wpedantic -O2)

add_library(gaussleg
  src/error.cpp
  src/error_explain.cpp
  src/logger.cpp
  src/quadrature.cpp)
target_include_directories(gaussleg
  PUBLIC include ${GAUSSLE_EIGEN_ROOT}
  PRIVATE src)

enable_testing()

add_executable(gaussleg_tests
  tests/test_main.cpp
  tests/test_handcraft.cpp
  tests/test_invariants.cpp
  tests/test_exactness.cpp
  tests/test_independent_ref.cpp
  tests/test_errors.cpp)
target_include_directories(gaussleg_tests PRIVATE src tests)
target_link_libraries(gaussleg_tests PRIVATE gaussleg)
add_test(NAME gaussleg_tests COMMAND gaussleg_tests)

add_executable(gaussleg_cli app/cli.cpp)
target_link_libraries(gaussleg_cli PRIVATE gaussleg)

add_executable(gaussleg_bench benchmarks/bench.cpp)
target_link_libraries(gaussleg_bench PRIVATE gaussleg)

add_executable(gw_reference_tool tools/gw_reference_tool.cpp
  tests/reference/golub_welsch.cpp)
target_include_directories(gw_reference_tool PRIVATE tests)
target_link_libraries(gw_reference_tool PRIVATE gaussleg)
