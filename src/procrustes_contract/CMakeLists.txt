add_library(procrustes_contract STATIC contract.cpp)
add_library(procrustes::contract ALIAS procrustes_contract)
target_include_directories(procrustes_contract PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(procrustes_contract
  PUBLIC eigen_headers PRIVATE project_options)
