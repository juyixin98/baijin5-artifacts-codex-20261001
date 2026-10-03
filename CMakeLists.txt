cmake_minimum_required(VERSION 3.20)
project(polyeval_tree LANGUAGES CXX VERSION 1.0.0)

set(POLYEVAL_VERSION "1.0.0")
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

# Vendored, locally fetched dependencies (see scripts/setup_deps.sh + deps.lock.json)
set(THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/third_party")
set(EIGEN_INCLUDE_DIR "${THIRD_PARTY}/eigen")
set(BOOST_INCLUDE_DIR "${THIRD_PARTY}/boost")

if(NOT EXISTS "${EIGEN_INCLUDE_DIR}/Eigen/Dense" OR NOT EXISTS "${BOOST_INCLUDE_DIR}/boost/multiprecision/cpp_int.hpp")
  message(FATAL_ERROR
    "Vendored headers missing. Run: bash scripts/setup_deps.sh\n"
    "  expected: ${EIGEN_INCLUDE_DIR}/Eigen/Dense\n"
    "  expected: ${BOOST_INCLUDE_DIR}/boost/multiprecision/cpp_int.hpp")
endif()

# --- Module 1: numeric contract (types, parsing, modes, failure categories) ---
add_library(polyeval_contract STATIC
  src/polyeval/contract.cpp src/polyeval/json.cpp src/polyeval/config.cpp
  src/polyeval/field.cpp)
target_include_directories(polyeval_contract PUBLIC include ${BOOST_INCLUDE_DIR})

# --- Module 2: algorithm kernel (product tree / remainder tree, header-only templates) ---
add_library(polyeval_kernel STATIC src/polyeval/kernel.cpp)
target_include_directories(polyeval_kernel PUBLIC include ${EIGEN_INCLUDE_DIR} ${BOOST_INCLUDE_DIR})
target_link_libraries(polyeval_kernel PUBLIC polyeval_contract)

# --- Module 3: error/uncertainty explainer ---
add_library(polyeval_explainer STATIC src/polyeval/explainer.cpp)
target_include_directories(polyeval_explainer PUBLIC include ${BOOST_INCLUDE_DIR})
target_link_libraries(polyeval_explainer PUBLIC polyeval_contract polyeval_kernel)

# --- Driver (orchestration + JSONL logging) shared by CLI ---
add_library(polyeval_driver STATIC src/polyeval/driver.cpp src/polyeval/log.cpp)
target_include_directories(polyeval_driver PUBLIC include ${BOOST_INCLUDE_DIR})
target_link_libraries(polyeval_driver PUBLIC polyeval_contract polyeval_kernel polyeval_explainer)

# --- Application: CLI ---
add_executable(polyeval src/cli/main.cpp)
target_link_libraries(polyeval PRIVATE polyeval_driver)

# --- Module 4: independent benchmark ---
add_executable(polyeval_bench bench/bench_main.cpp)
target_link_libraries(polyeval_bench PRIVATE polyeval_kernel polyeval_contract)

# --- Independent tests ---
enable_testing()
add_executable(polyeval_tests tests/test_main.cpp)
target_link_libraries(polyeval_tests PRIVATE polyeval_driver)
add_test(NAME unit_and_contract_tests COMMAND polyeval_tests)
add_test(NAME cli_smoke
  COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/scripts/cli_smoke.sh
          $<TARGET_FILE:polyeval> ${CMAKE_CURRENT_SOURCE_DIR})
set_tests_properties(cli_smoke PROPERTIES TIMEOUT 60)
