add_executable(procrustes_fit src/main.cpp src/io_csv.cpp src/config_ini.cpp)
target_include_directories(procrustes_fit PRIVATE include)
target_compile_features(procrustes_fit PRIVATE cxx_std_20)
target_link_libraries(procrustes_fit PRIVATE kernel contracts diag Eigen3::Eigen)
