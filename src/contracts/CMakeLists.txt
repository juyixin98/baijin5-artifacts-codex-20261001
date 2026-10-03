add_library(contracts STATIC src/contracts.cpp)
target_include_directories(contracts PUBLIC include)
target_compile_features(contracts PUBLIC cxx_std_20)
target_link_libraries(contracts PUBLIC Eigen3::Eigen)
