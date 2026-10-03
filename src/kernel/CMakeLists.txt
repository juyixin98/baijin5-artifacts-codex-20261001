add_library(kernel STATIC src/procrustes.cpp)
target_include_directories(kernel PUBLIC include)
target_compile_features(kernel PUBLIC cxx_std_20)
target_link_libraries(kernel PUBLIC contracts diag Eigen3::Eigen)
