add_library(procrustes_bench STATIC bench.cpp)
add_library(procrustes::bench ALIAS procrustes_bench)
target_include_directories(procrustes_bench PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(procrustes_bench
  PUBLIC procrustes::contract PRIVATE project_options)
