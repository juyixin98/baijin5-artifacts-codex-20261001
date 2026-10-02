add_library(procrustes_core STATIC fit.cpp)
add_library(procrustes::core ALIAS procrustes_core)
target_include_directories(procrustes_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(procrustes_core
  PUBLIC procrustes::contract PRIVATE project_options)
