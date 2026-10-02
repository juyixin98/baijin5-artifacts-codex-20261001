# Project-local, pinned third-party dependencies (no system install required).
# Run ./tools/fetch_deps.sh to populate ${MP_DEPS_DIR}.
set(MP_DEPS_DIR "${CMAKE_SOURCE_DIR}/deps" CACHE PATH "Directory holding extracted local dependencies")
set(MP_BOOST_ROOT "${MP_DEPS_DIR}/boost_1_86_0" CACHE PATH "Extracted Boost 1.86.0 root")
set(MP_EIGEN_ROOT "${MP_DEPS_DIR}/eigen-3.4.0" CACHE PATH "Extracted Eigen 3.4.0 root")

add_library(mp_boost INTERFACE)
target_include_directories(mp_boost SYSTEM BEFORE INTERFACE "${MP_BOOST_ROOT}")

add_library(mp_eigen INTERFACE)
target_include_directories(mp_eigen SYSTEM BEFORE INTERFACE "${MP_EIGEN_ROOT}")

if(NOT EXISTS "${MP_BOOST_ROOT}/boost/version.hpp")
  message(FATAL_ERROR "Boost headers missing under ${MP_BOOST_ROOT}. Run ./tools/fetch_deps.sh first.")
endif()
if(NOT EXISTS "${MP_EIGEN_ROOT}/Eigen/Core")
  message(FATAL_ERROR "Eigen headers missing under ${MP_EIGEN_ROOT}. Run ./tools/fetch_deps.sh first.")
endif()
