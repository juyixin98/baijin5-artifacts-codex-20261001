add_library(procrustes_explain STATIC explain.cpp)
add_library(procrustes::explain ALIAS procrustes_explain)
target_include_directories(procrustes_explain PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(procrustes_explain
  PUBLIC procrustes::contract PRIVATE project_options)
