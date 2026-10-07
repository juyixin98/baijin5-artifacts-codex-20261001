add_executable(gen_data gen_data.cpp ${CMAKE_SOURCE_DIR}/src/app/src/io_csv.cpp)
target_include_directories(gen_data PRIVATE
  ${CMAKE_SOURCE_DIR}/src/app/include)
target_compile_features(gen_data PRIVATE cxx_std_20)
target_link_libraries(gen_data PRIVATE contracts Eigen3::Eigen)
