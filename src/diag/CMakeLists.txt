add_library(diag STATIC src/logger.cpp src/report.cpp)
target_include_directories(diag PUBLIC include)
target_compile_features(diag PUBLIC cxx_std_20)
target_link_libraries(diag PUBLIC contracts Eigen3::Eigen)
