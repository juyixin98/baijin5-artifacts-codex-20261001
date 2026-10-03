add_executable(test_end_to_end test_end_to_end.cpp
  ${CMAKE_SOURCE_DIR}/src/app/src/io_csv.cpp)
target_include_directories(test_end_to_end PRIVATE
  ${CMAKE_SOURCE_DIR}/src/app/include)
target_link_libraries(test_end_to_end PRIVATE kernel contracts diag)
add_test(NAME end_to_end_fixtures
         COMMAND test_end_to_end ${CMAKE_SOURCE_DIR}/data)

find_program(BASH_PROGRAM bash)
if(BASH_PROGRAM)
  add_test(NAME cli_shell_integration
           COMMAND ${BASH_PROGRAM}
                   ${CMAKE_SOURCE_DIR}/tests/integration/test_cli.sh
           WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endif()
