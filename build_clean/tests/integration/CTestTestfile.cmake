# CMake generated Testfile for 
# Source directory: /home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration
# Build directory: /home/admin/Downloads/xinbiaozhul/opp839/b/build_clean/tests/integration
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test([=[end_to_end_fixtures]=] "/home/admin/Downloads/xinbiaozhul/opp839/b/build_clean/tests/integration/test_end_to_end" "/home/admin/Downloads/xinbiaozhul/opp839/b/data")
set_tests_properties([=[end_to_end_fixtures]=] PROPERTIES  _BACKTRACE_TRIPLES "/home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration/CMakeLists.txt;6;add_test;/home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration/CMakeLists.txt;0;")
add_test([=[cli_shell_integration]=] "/usr/bin/bash" "/home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration/test_cli.sh")
set_tests_properties([=[cli_shell_integration]=] PROPERTIES  WORKING_DIRECTORY "/home/admin/Downloads/xinbiaozhul/opp839/b" _BACKTRACE_TRIPLES "/home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration/CMakeLists.txt;11;add_test;/home/admin/Downloads/xinbiaozhul/opp839/b/tests/integration/CMakeLists.txt;0;")
