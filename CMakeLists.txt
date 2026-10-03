cmake_minimum_required(VERSION 3.20)
project(procrustes_fit LANGUAGES CXX VERSION 1.0.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

set(PROC_VERSION "1.0.0")

set(EIGEN_LOCAL_DIR "${CMAKE_SOURCE_DIR}/third_party/eigen-3.4.0")
if(EXISTS "${EIGEN_LOCAL_DIR}/Eigen/Core")
  add_library(eigen_proxy INTERFACE)
  target_include_directories(eigen_proxy SYSTEM BEFORE INTERFACE "${EIGEN_LOCAL_DIR}")
  add_library(Eigen3::Eigen ALIAS eigen_proxy)
  message(STATUS "Using local Eigen at ${EIGEN_LOCAL_DIR}")
else()
  message(FATAL_ERROR "Eigen not found at ${EIGEN_LOCAL_DIR}; run scripts/setup_deps.sh")
endif()

enable_testing()
add_subdirectory(src/contracts)
add_subdirectory(src/diag)
add_subdirectory(src/kernel)
add_subdirectory(src/app)
add_subdirectory(tools/gen_data)
add_subdirectory(bench)
add_subdirectory(tests/unit)

# Generate synthetic fixtures at configure time using the independent generator.
add_custom_command(
  OUTPUT ${CMAKE_SOURCE_DIR}/data/rigid2d_pairs.csv
         ${CMAKE_SOURCE_DIR}/data/similarity2d_pairs.csv
         ${CMAKE_SOURCE_DIR}/data/rigid3d_pairs.csv
         ${CMAKE_SOURCE_DIR}/data/collinear2d_pairs.csv
  COMMAND $<TARGET_FILE:gen_data> rigid2d ${CMAKE_SOURCE_DIR}/data
  COMMAND $<TARGET_FILE:gen_data> similarity2d ${CMAKE_SOURCE_DIR}/data
  COMMAND $<TARGET_FILE:gen_data> rigid3d ${CMAKE_SOURCE_DIR}/data
  COMMAND $<TARGET_FILE:gen_data> collinear2d ${CMAKE_SOURCE_DIR}/data
  DEPENDS gen_data
  COMMENT "Generating independent synthetic fixtures in data/")
add_custom_target(generate_data ALL DEPENDS
  ${CMAKE_SOURCE_DIR}/data/rigid2d_pairs.csv)

add_subdirectory(tests/integration)
