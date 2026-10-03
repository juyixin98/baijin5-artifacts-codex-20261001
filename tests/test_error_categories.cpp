// Independent assertions on concrete failure CATEGORIES (not just "the call
// worked"): empty input, bad length arguments, NaN/Inf inputs, memory budget
// exceeded, and invalid sign.
#include "mathcore/fft.hpp"
#include "reference/reference_dft.hpp"
#include "test_framework.hpp"
#include <cmath>
#include <limits>
#include <vector>

using fft::mathcore::Cmplx;
namespace mc = fft::mathcore;
using fft::common::FftOptions;
using fft::common::FftStatus;

int main() {
  tf::begin("empty input -> EMPTY_INPUT (forward and inverse)");
  {
    std::vector<Cmplx> empty;
    auto a = mc::fft(empty);
    auto b = mc::ifft(empty);
    tf::check(a.error.status == FftStatus::EmptyInput, "fft empty");
    tf::check(b.error.status == FftStatus::EmptyInput, "ifft empty");
  }

  tf::begin("invalid sign -> INVALID_ARGUMENT");
  {
    std::vector<Cmplx> x(8, Cmplx(1, 0));
    auto r = mc::transform(x, 0);
    tf::check(r.error.status == FftStatus::InvalidArgument, "sign 0");
    auto r2 = mc::transform(x, 7);
    tf::check(r2.error.status == FftStatus::InvalidArgument, "sign 7");
  }

  tf::begin("NaN input -> VALUE_OUT_OF_DOMAIN");
  {
    std::vector<Cmplx> x(16, Cmplx(1, 0));
    x[5] = Cmplx(std::numeric_limits<double>::quiet_NaN(), 0);
    auto r = mc::fft(x);
    tf::check(r.error.status == FftStatus::ValueOutOfDomain, "nan");
  }
  tf::begin("Inf input -> VALUE_OUT_OF_DOMAIN (bluestein path too)");
  {
    std::vector<Cmplx> x(17, Cmplx(1, 0));
    x[2] = Cmplx(0, std::numeric_limits<double>::infinity());
    auto r = mc::fft(x);
    tf::check(r.error.status == FftStatus::ValueOutOfDomain, "inf");
  }

  tf::begin("tiny memory budget -> ALLOCATION_FAILED with concrete budget");
  {
    FftOptions opt;
    opt.memoryBudgetBytes = 64; // cannot even hold an N=8 pair of buffers
    std::vector<Cmplx> x(8, Cmplx(1, 0));
    auto r = mc::fft(x, opt);
    tf::check(r.error.status == FftStatus::AllocationFailed,
              std::string("expected allocation failure, got ") +
                  fft::common::statusName(r.error.status));
    tf::check(!r.error.message.empty(), "must explain budget");
  }
  tf::begin("memory budget fails bluestein path category too");
  {
    FftOptions opt;
    opt.memoryBudgetBytes = 4096;
    std::vector<Cmplx> x(1000, Cmplx(1, 0));
    auto r = mc::fft(x, opt);
    tf::check(r.error.status == FftStatus::AllocationFailed,
              "bluestein allocation category");
  }

  tf::begin("reference DFT enforces its own argument rules");
  {
    std::vector<std::complex<long double>> empty;
    auto e1 = fft::reference::directDft(empty, -1);
    tf::check(e1.error.status == FftStatus::EmptyInput, "ref empty");
    std::vector<std::complex<long double>> x(4, {1.0L, 0.0L});
    auto e2 = fft::reference::directDft(x, 2);
    tf::check(e2.error.status == FftStatus::InvalidArgument, "ref sign");
  }
  return tf::finish();
}
