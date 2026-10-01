// SPDX-License-Identifier: MIT
#include "fft/kernel.hh"
#include "test_framework.hh"

#include <iostream>

int main() {
  int failed_cases = 0;
  for (auto& c : ::tst::registry()) {
    const int before = ::tst::failures();
    std::cout << "[ RUN  ] " << c.name << "\n";
    c.fn();
    if (::tst::failures() == before)
      std::cout << "[  OK  ] " << c.name << "\n";
    else {
      std::cout << "[ FAIL ] " << c.name << "\n";
      ++failed_cases;
    }
  }
  std::cout << "\n" << ::tst::checks() << " assertions, "
            << ::tst::failures() << " failed assertions across "
            << failed_cases << " case(s).\n";
  return failed_cases == 0 ? 0 : 1;
}
