add_executable(procrustes_cli procrustes_cli.cpp)
target_include_directories(procrustes_cli PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(procrustes_cli PRIVATE
  procrustes::core procrustes::explain project_options)

add_executable(datagen datagen.cpp)
target_include_directories(datagen PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(datagen PRIVATE procrustes::contract project_options)

add_executable(bench_main bench_main.cpp)
target_include_directories(bench_main PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(bench_main PRIVATE
  procrustes::core procrustes::explain procrustes::bench project_options)
